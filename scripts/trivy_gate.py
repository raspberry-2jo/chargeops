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
import trivy_exceptions as exceptions

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


def read_report(path, policy=None, image=None, audit=None):
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        raise GateError('Trivy JSON report is missing or malformed: '+path.name) from None
    if (not isinstance(data,dict) or data.get('SchemaVersion')!=2
            or not isinstance(data.get('ArtifactName'),str) or not data['ArtifactName']
            or (data.get('Results') is not None and not isinstance(data['Results'],list))):
        raise GateError('Trivy JSON report has an unexpected schema: '+path.name)
    review=exceptions.evaluate(data,policy or {'exceptions':[]},image)
    if audit is not None:
        audit.update(review)
    return review['blocked']


def verify_runtime(identity, service):
    result=command('docker','run','--rm','--network','none','--read-only',
                   '--cap-drop','ALL','--security-opt','no-new-privileges',
                   '--entrypoint','python',identity,'-m','runtime_security',service,
                   capture_output=True,text=True)
    try:
        evidence=json.loads(result.stdout)
    except (ValueError,TypeError):
        raise GateError('Missing runtime mitigation evidence; exception blocked') from None
    if (evidence.get('uid')!=10001 or evidence.get('reviewed_tools_absent') is not True
            or evidence.get('suid_sgid_absent') is not True
            or evidence.get('runtime_imports_passed') is not True
            or evidence.get('service')!=service):
        raise GateError('Runtime mitigation requirements failed; exception blocked')
    return evidence


def scanner_command(scratch, cache, reports):
    return ['docker','run','--rm','--pull=never',
            '-v',f'{scratch}:/scan:Z','-v',f'{cache}:/root/.cache:Z',
            '-v',f'{reports}:/reports:Z',SCANNER]


def scan(base, mode, report, target, policy=None, image=None, audit=None):
    args=base+[mode]
    if mode=='image':args+=['--input',target,'--scanners','vuln']
    args+=['--severity','HIGH,CRITICAL','--exit-code','42','--format','json',
           '--config','','--ignorefile','/scan/no-ignores','--output','/reports/'+report.name,'--no-progress']
    if mode=='config':args.append(target)
    started=time.monotonic()
    finding_exit=False
    # 42 means findings only; other nonzero exits remain scanner failures.
    try:
        command(*args)
    except subprocess.CalledProcessError as error:
        if error.returncode!=42:
            raise GateError(f'Trivy {mode} failed (exit {error.returncode}); publication blocked') from None
        finding_exit=True
    review={}
    blocked=read_report(report,policy,image,review)
    if audit is not None:
        audit.update(review)
    if finding_exit and not review['total']:
        raise GateError('Trivy finding exit without valid findings; publication blocked')
    if blocked:
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
        policy=exceptions.load(root/'security/trivy-exceptions.json')
        receipt['exception_policy_sha256']=exceptions.digest(policy)
        receipt['reviews']={}
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
                    review={}
                    receipt['reviews'][name]=review
                    receipt['timings'][name]['scan_seconds']=scan(base,'image',reports/(name+'.json'),
                        '/scan/image.tar',policy,name,review)
                    if review['excepted']:
                        review['runtime_evidence']=verify_runtime(identity,name)
                    print(f"Trivy {name}: total={review['total']}, exceptions={review['excepted']}, blocked={review['blocked']}",flush=True)
                    for entry in review['exceptions']:
                        print(f"  ACCEPTED {entry['cve']} {entry['package']} until {entry['expires_on']}",flush=True)
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
    except (GateError,OSError,ValueError,subprocess.CalledProcessError,tarfile.TarError) as error:
        receipt['error']=str(error) if isinstance(error,GateError) else type(error).__name__
        raise GateError('Trivy gate failed; see build reports and console. '+receipt['error']) from None
    finally:
        receipt['timings']['total_seconds']=round(time.monotonic()-started,3)
        receipt_path.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        accepted=sum(item.get('excepted',0) for item in receipt.get('reviews',{}).values())
        unique={entry['cve'] for item in receipt.get('reviews',{}).values() for entry in item.get('exceptions',[])}
        summary=f"Trivy {receipt['status'].upper()}: exceptions={accepted} findings / {len(unique)} CVEs"
        (reports/'summary.txt').write_text(summary+'\n',encoding='utf-8')
    print(summary+'; all selected images and configuration passed policy; publication may proceed',flush=True)


def verify(root,build,selected,registry,tag):
    reports,_=paths(root,build)
    try:receipt=json.loads((reports/'gate.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):raise GateError('Missing valid Trivy gate receipt; publication blocked') from None
    refs=references(root,selected,registry,tag)
    policy=exceptions.load(root/'security/trivy-exceptions.json')
    if (receipt.get('schema')!=1 or receipt.get('status')!='passed' or receipt.get('build')!=build
            or receipt.get('scanner')!=SCANNER or receipt.get('source')!=source_commit(root)
            or set(receipt.get('images',{}))!=set(refs)
            or receipt.get('exception_policy_sha256')!=exceptions.digest(policy)):
        raise GateError('Trivy gate did not pass for this exact build/source/image selection')
    for name,reference in refs.items():
        item=receipt['images'][name]
        if item.get('reference')!=reference or item.get('id')!=image_id(reference):
            raise GateError('Image differs from the scanned image; publication blocked')
        review={}
        if item.get('report')!=name+'.json' or read_report(reports/(name+'.json'),policy,name,review):
            raise GateError('Image report is invalid or contains HIGH/CRITICAL findings')
        if review['excepted']:
            verify_runtime(item['id'],name)
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
    except (GateError,OSError,ValueError,subprocess.CalledProcessError) as error:
        print(str(error),flush=True)
        return 1
    except KeyboardInterrupt:
        print('Trivy gate interrupted; publication blocked',flush=True)
        return 130
    return 0


if __name__=='__main__':raise SystemExit(main())
