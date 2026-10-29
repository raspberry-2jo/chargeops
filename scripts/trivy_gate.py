"""Fail-closed Trivy gate for every selected local image, before any publication.

The scanner gets only scratch/cache/reports mounts, never Docker's socket,
credentials, or the entire Jenkins workspace. Python 3.12+ on the Jenkins agent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import tarfile
import tempfile
import time

import ci

SCANNER = 'aquasec/trivy:0.76.1'


class GateError(RuntimeError):
    pass


def command(*args, **kwargs):
    return subprocess.run(list(args), check=True, **kwargs)


def source_commit(root):
    return command('git','rev-parse','HEAD',cwd=root,capture_output=True,text=True).stdout.strip()


def image_id(reference):
    value=command('docker','image','inspect','--format','{{.Id}}',reference,
                  capture_output=True,text=True).stdout.strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',value):
        raise GateError('Docker did not return a unique local image ID')
    return value


def references(root, selected, registry, tag):
    allowed={image.name for image in ci.image_definitions(root)}
    names=selected.split()
    if not names or len(names)!=len(set(names)) or any(name not in allowed for name in names):
        raise GateError('Select distinct known service image names')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*',registry) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',tag):
        raise GateError('Invalid registry or image tag')
    return {name:f'{registry}/{name}:{tag}' for name in names}


def read_report(path):
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        raise GateError('Trivy JSON report is missing or malformed: '+path.name) from None
    if (not isinstance(data,dict) or data.get('SchemaVersion')!=2
            or not isinstance(data.get('ArtifactName'),str) or not data['ArtifactName']
            or (data.get('Results') is not None and not isinstance(data['Results'],list))):
        raise GateError('Trivy JSON report has an unexpected schema: '+path.name)
    findings=0
    for result in data.get('Results') or []:
        for key in ('Vulnerabilities','Misconfigurations'):
            for item in result.get(key) or []:
                if item.get('Severity') in ('HIGH','CRITICAL'):
                    findings+=1
    return findings


def scanner_command(scratch, cache, reports):
    return ['docker','run','--rm','--pull=never',
            '-v',f'{scratch}:/scan:Z','-v',f'{cache}:/root/.cache:Z',
            '-v',f'{reports}:/reports:Z',SCANNER]


def scan(base, mode, report, target):
    args=base+[mode]
    if mode=='image':args+=['--input',target,'--scanners','vuln']
    args+=['--severity','HIGH,CRITICAL','--exit-code','1','--format','json',
           '--config','','--ignorefile','/scan/no-ignores','--output','/reports/'+report.name,'--no-progress']
    if mode=='config':args.append(target)
    started=time.monotonic()
    # Both a finding exit and a scanner error are failures. Preserve emitted JSON.
    try:
        command(*args)
    except subprocess.CalledProcessError as error:
        raise GateError(f'Trivy {mode} failed (exit {error.returncode}); publication blocked') from None
    if read_report(report):
        raise GateError('HIGH/CRITICAL findings remain in '+report.name)
    return round(time.monotonic()-started,3)


def paths(root, build):
    if not re.fullmatch(r'[0-9]+',build):
        raise GateError('Jenkins BUILD_NUMBER is required')
    reports=root/'trivy-reports'/build
    cache=root/'.trivy-cache'
    # Do not traverse a stale/malicious output symlink from the checkout.
    for path in (root/'trivy-reports',reports,cache):
        if path.is_symlink():raise GateError('Trivy output/cache path must not be a symlink')
    return reports,cache


def run(root,build,selected,registry,tag):
    reports,cache=paths(root,build)
    reports.mkdir(parents=True,exist_ok=True)
    receipt={'schema':1,'build':build,'scanner':SCANNER,'status':'failed','images':{},'timings':{}}
    receipt_path=reports/'gate.json'
    # Invalidate a prior successful attempt of this same Jenkins build before scanning.
    receipt_path.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    started=time.monotonic()
    try:
        cache.mkdir(parents=True,exist_ok=True)
        refs=references(root,selected,registry,tag)
        receipt['source']=source_commit(root)
        command('docker','image','inspect',SCANNER,capture_output=True,text=True)
        # Only this build's known report files can be replaced on a restart.
        for name in [*(name+'.json' for name in refs),'config.json']:
            path=reports/name
            if path.is_symlink():raise GateError('Trivy report must not be a symlink')
            path.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix='.trivy-scan-',dir=root) as temporary:
            scratch=Path(temporary)
            (scratch/'no-ignores').write_text('',encoding='utf-8')
            base=scanner_command(scratch,cache,reports)
            for name,reference in refs.items():
                print('Trivy image gate: '+reference,flush=True)
                identity=image_id(reference)
                archive=scratch/'image.tar'
                saved=time.monotonic()
                try:
                    command('docker','save','--output',str(archive),reference)
                    if not archive.is_file() or archive.stat().st_size<=0:
                        raise GateError('Docker save did not produce a nonempty archive')
                    receipt['timings'][name]={'docker_save_seconds':round(time.monotonic()-saved,3),
                                            'tar_bytes':archive.stat().st_size}
                    receipt['timings'][name]['scan_seconds']=scan(base,'image',reports/(name+'.json'),'/scan/image.tar')
                finally:
                    archive.unlink(missing_ok=True)
                if image_id(reference)!=identity:
                    raise GateError('Local image changed during scanning')
                receipt['images'][name]={'reference':reference,'id':identity,'report':name+'.json'}
            # Include tracked Dockerfile/IaC sources, excluding old workspaces, auth,
            # .gitops-delivery, virtualenvs, reports and the Trivy cache.
            source_tar=scratch/'source.tar'
            with source_tar.open('wb') as stream:
                command('git','archive','--format=tar','HEAD',cwd=root,stdout=stream)
            source=scratch/'source';source.mkdir()
            with tarfile.open(source_tar) as archive:
                archive.extractall(source,filter='data')
            print('Trivy configuration gate: tracked checkout '+receipt['source'],flush=True)
            receipt['timings']['config_scan_seconds']=scan(base,'config',reports/'config.json','/scan/source')
            if source_commit(root)!=receipt['source']:
                raise GateError('Checkout changed during scanning')
        receipt['status']='passed'
    except (GateError,OSError,subprocess.CalledProcessError,tarfile.TarError) as error:
        receipt['error']=str(error) if isinstance(error,GateError) else type(error).__name__
        raise GateError('Trivy gate failed; see build reports and console. '+receipt['error']) from None
    finally:
        receipt['timings']['total_seconds']=round(time.monotonic()-started,3)
        receipt_path.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print('Trivy gate passed: all selected local images and tracked configuration; publication may proceed',flush=True)


def verify(root,build,selected,registry,tag):
    reports,_=paths(root,build)
    try:receipt=json.loads((reports/'gate.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):raise GateError('Missing valid Trivy gate receipt; publication blocked') from None
    refs=references(root,selected,registry,tag)
    if (receipt.get('schema')!=1 or receipt.get('status')!='passed' or receipt.get('build')!=build
            or receipt.get('scanner')!=SCANNER or receipt.get('source')!=source_commit(root)
            or set(receipt.get('images',{}))!=set(refs)):
        raise GateError('Trivy gate did not pass for this exact build/source/image selection')
    for name,reference in refs.items():
        item=receipt['images'][name]
        if item.get('reference')!=reference or item.get('id')!=image_id(reference):
            raise GateError('Image differs from the scanned image; publication blocked')
        if item.get('report')!=name+'.json' or read_report(reports/(name+'.json')):
            raise GateError('Image report is invalid or contains HIGH/CRITICAL findings')
    if read_report(reports/'config.json'):
        raise GateError('Configuration report contains HIGH/CRITICAL findings')
    print('Verified current build Trivy receipt and local image IDs',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['run','verify'])
    parser.add_argument('--build-number',required=True)
    parser.add_argument('--images',required=True)
    parser.add_argument('--registry',required=True)
    parser.add_argument('--tag',required=True)
    args=parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Trivy gate interrupted; publication blocked')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        (run if args.command=='run' else verify)(Path.cwd().resolve(),args.build_number,args.images,args.registry,args.tag)
    except (GateError,OSError,subprocess.CalledProcessError) as error:
        print(str(error),flush=True)
        return 1
    except KeyboardInterrupt:
        print('Trivy gate interrupted; publication blocked',flush=True)
        return 130
    return 0


if __name__=='__main__':raise SystemExit(main())
