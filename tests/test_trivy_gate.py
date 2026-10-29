"""Exercise the real gate with deterministic Docker/Git adapters; no CVE DB needed."""
import hashlib
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('trivy_gate',ROOT/'scripts/trivy_gate.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
NAMES=['chargeops-ai-gateway','chargeops-chatops-bot','chargeops-csms',
       'chargeops-notifier','chargeops-vcharger','chargeops-collector']
REGISTRY='registry.chargeops.lab:5000/chargeops'


@pytest.fixture
def gate(tmp_path,monkeypatch):
    (tmp_path/'Jenkinsfile').write_text((ROOT/'Jenkinsfile').read_text(encoding='utf-8'),encoding='utf-8')
    state={'calls':[],'failure':None,'changed':False,'save_paths':[]}
    def fake(*args,**kwargs):
        state['calls'].append(args)
        if args[:3]==('git','rev-parse','HEAD'):
            return subprocess.CompletedProcess(args,0,stdout='a'*40+'\n')
        if args[:2]==('git','archive'):
            assert args[3:5]==('HEAD','--')
            assert len(args[5:])==6 and all(path.startswith('apps/') and path.endswith('/Dockerfile') for path in args[5:])
            with tarfile.open(fileobj=kwargs['stdout'],mode='w') as archive:
                data=b'FROM python:3.13-slim\nUSER 10001\n'
                item=tarfile.TarInfo('apps/csms/Dockerfile');item.size=len(data)
                archive.addfile(item,io.BytesIO(data))
            return subprocess.CompletedProcess(args,0)
        if args[:3]==('docker','image','inspect'):
            if args[-1]==g.SCANNER:
                if state['failure']=='tool':raise subprocess.CalledProcessError(1,args)
                return subprocess.CompletedProcess(args,0,stdout='[]')
            identity=hashlib.sha256(args[-1].encode()).hexdigest()
            return subprocess.CompletedProcess(args,0,stdout='sha256:'+('b'*64 if state['changed'] else identity)+'\n')
        if args[:2]==('docker','save'):
            path=Path(args[args.index('--output')+1]);path.write_bytes(b'archive');state['save_paths'].append(path)
            if state['failure']=='save':raise subprocess.CalledProcessError(1,args)
            return subprocess.CompletedProcess(args,0)
        assert args[:2]==('docker','run')
        if '--entrypoint' in args:
            assert '--read-only' in args and '--network' in args and 'none' in args
            assert '--cap-drop' in args and 'ALL' in args and 'no-new-privileges' in args
            if state['failure']=='runtime':raise subprocess.CalledProcessError(1,args)
            return subprocess.CompletedProcess(args,0,stdout=json.dumps({'uid':10001,
                'reviewed_tools_absent':True,'suid_sgid_absent':True,'runtime_imports_passed':True,
                'service':args[-1]}))
        assert '--pull=never' in args
        mounts=[args[i+1] for i,value in enumerate(args) if value=='-v']
        assert not any('docker.sock' in mount or '/root/.docker' in mount for mount in mounts)
        scratch=Path(next(mount[:-len(':/scan:Z')] for mount in mounts if mount.endswith(':/scan:Z')))
        assert scratch!=tmp_path
        assert (scratch/'no-ignores').read_text()==''
        mode=args[args.index(g.SCANNER)+1]
        if '--help' in args:
            flags='--input --scanners --no-progress ' if mode=='image' else ''
            flags+='--severity --exit-code --format --config --ignorefile --output'
            if state['failure']=='unsupported-flag':flags=flags.replace('--format','')
            return subprocess.CompletedProcess(args,0,stdout=flags)
        if mode=='image':assert (scratch/'image.tar').is_file()
        else:
            assert '--no-progress' not in args, 'Trivy config 0.76.1 does not support --no-progress'
            assert (scratch/'source/apps/csms/Dockerfile').is_file()
            assert not (scratch/'source/.trivy-cache').exists()
        if state['failure']=='download':raise subprocess.CalledProcessError(1,args)
        report=tmp_path/'trivy-reports/21'/args[args.index('--output')+1].rsplit('/',1)[1]
        if state['failure']=='missing_report':return subprocess.CompletedProcess(args,0)
        if state['failure']=='malformed':
            report.write_text('{broken')
            return subprocess.CompletedProcess(args,0)
        if mode=='image' and state.get('finding_data'):
            report.write_text(json.dumps(state['finding_data']))
            raise subprocess.CalledProcessError(state.get('scanner_exit',42),args)
        finding=(mode=='image' and state['failure'] in ('cve','silent_cve')) or (mode=='config' and state['failure']=='config')
        key='Vulnerabilities' if mode=='image' else 'Misconfigurations'
        report.write_text(json.dumps({'SchemaVersion':2,'ArtifactName':'fixture',
            'Results':[{key:[{'Severity':'HIGH'}] if finding else []}]}))
        if finding and state['failure']!='silent_cve':raise subprocess.CalledProcessError(42,args)
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(g,'command',fake)
    return tmp_path,state


def execute(root,names=NAMES):
    g.run(root,'21',' '.join(names),REGISTRY,'build-21')


def test_all_selected_images_and_config_are_scanned_before_receipt_allows_push(gate):
    root,state=gate
    execute(root)
    g.verify(root,'21',' '.join(NAMES),REGISTRY,'build-21')
    receipt=json.loads((root/'trivy-reports/21/gate.json').read_text())
    assert receipt['status']=='passed' and set(receipt['images'])==set(NAMES)
    assert receipt['configuration_scope']['aws_iac_scanned'] is False
    assert receipt['configuration_scope']['kubernetes_examples_scanned'] is False
    assert len(receipt['configuration_scope']['files'])==6
    scans=[a for a in state['calls'] if a[:2]==('docker','run') and '--help' not in a]
    assert [a[a.index(g.SCANNER)+1] for a in scans]==['image']*6+['config']
    assert len(list((root/'trivy-reports/21').glob('*.json')))==8
    assert all(not path.exists() for path in state['save_paths'])
    assert not list(root.glob('.trivy-scan-*'))
    assert all('push' not in a for a in state['calls'])
    for args in scans:
        assert args[args.index('--severity')+1]=='HIGH,CRITICAL'
        assert args[args.index('--exit-code')+1]=='42'


@pytest.mark.parametrize('failure',['cve','config','save','download','tool','missing_report','malformed','silent_cve','unsupported-flag'])
def test_every_policy_or_scan_failure_blocks_publication_and_cleans_archives(gate,failure):
    root,state=gate;state['failure']=failure
    with pytest.raises(g.GateError):execute(root)
    assert json.loads((root/'trivy-reports/21/gate.json').read_text())['status']=='failed'
    assert not list(root.glob('.trivy-scan-*'))
    assert all(not path.exists() for path in state['save_paths'])
    with pytest.raises(g.GateError):g.verify(root,'21',' '.join(NAMES),REGISTRY,'build-21')
    if failure in ('cve','config','silent_cve'):
        assert any(p.name!='gate.json' for p in (root/'trivy-reports/21').glob('*.json'))


def test_changed_image_missing_report_or_different_selection_cannot_reuse_success(gate):
    root,state=gate;execute(root)
    with pytest.raises(g.GateError):g.verify(root,'21',NAMES[0],REGISTRY,'build-21')
    state['changed']=True
    with pytest.raises(g.GateError,match='differs'):g.verify(root,'21',' '.join(NAMES),REGISTRY,'build-21')
    state['changed']=False
    (root/'trivy-reports/21/config.json').unlink()
    with pytest.raises(g.GateError,match='missing'):g.verify(root,'21',' '.join(NAMES),REGISTRY,'build-21')


def test_missing_receipt_cannot_restart_directly_at_push(gate):
    root,_=gate
    with pytest.raises(g.GateError,match='receipt'):g.verify(root,'21',' '.join(NAMES),REGISTRY,'build-21')


def test_same_build_failed_retry_invalidates_prior_success(gate):
    root,state=gate;execute(root)
    state['failure']='download'
    with pytest.raises(g.GateError):execute(root)
    with pytest.raises(g.GateError):g.verify(root,'21',' '.join(NAMES),REGISTRY,'build-21')


def test_summary_separates_accepted_exceptions_from_config_blockers(gate,monkeypatch):
    root,state=enable_exception(gate,monkeypatch)
    state['failure']='config'
    with pytest.raises(g.GateError):execute(root,[NAMES[0]])
    text=(root/'trivy-reports/21/summary.txt').read_text()
    assert 'FAILED' in text and 'blocked=1' in text and 'accepted_exceptions=1' in text


def test_interruption_retains_failed_receipt_and_removes_tar(gate,monkeypatch):
    root,state=gate;original=g.command
    def interrupted(*args,**kwargs):
        if args[:2]==('docker','run'):raise KeyboardInterrupt()
        return original(*args,**kwargs)
    monkeypatch.setattr(g,'command',interrupted)
    with pytest.raises(KeyboardInterrupt):execute(root)
    assert json.loads((root/'trivy-reports/21/gate.json').read_text())['status']=='failed'
    assert all(not path.exists() for path in state['save_paths'])


@pytest.mark.parametrize('names',['','typo','chargeops-csms chargeops-csms'])
def test_unknown_empty_or_duplicate_images_rejected(gate,names):
    root,state=gate
    with pytest.raises(g.GateError):g.run(root,'21',names,REGISTRY,'build-21')
    assert not state['calls']


def enable_exception(gate,monkeypatch):
    root,state=gate
    (root/'security').mkdir()
    (root/'security/trivy-exceptions.json').write_text((ROOT/'security/trivy-exceptions.json').read_text())
    evaluate=g.exceptions.evaluate
    monkeypatch.setattr(g.exceptions,'evaluate',lambda *a,**kw:evaluate(*a,**kw,today=date(2026,10,29)))
    state['finding_data']={'SchemaVersion':2,'ArtifactName':'fixture',
        'Metadata':{'OS':{'Family':'debian','Name':'13.7'}},'Results':[{'Class':'os-pkgs','Type':'debian',
        'Vulnerabilities':[{'VulnerabilityID':'CVE-2026-76642','PkgName':'bsdutils',
            'InstalledVersion':'1:2.41.5-0+deb13u1','Severity':'HIGH'}]}]}
    return root,state


@pytest.mark.parametrize('image',NAMES)
def test_reviewed_exception_requires_mitigated_image_and_retains_original_report(gate,monkeypatch,image):
    root,state=enable_exception(gate,monkeypatch)
    execute(root,[image])
    g.verify(root,'21',image,REGISTRY,'build-21')
    original=json.loads((root/'trivy-reports/21'/(image+'.json')).read_text())
    assert original==state['finding_data']
    receipt=json.loads((root/'trivy-reports/21/gate.json').read_text())
    assert receipt['reviews'][image]['excepted']==1
    assert receipt['reviews'][image]['runtime_evidence']['service']==image
    assert 'exceptions=1' in (root/'trivy-reports/21/summary.txt').read_text()
    (root/'security/trivy-exceptions.json').unlink()
    with pytest.raises(g.GateError):g.verify(root,'21',image,REGISTRY,'build-21')


@pytest.mark.parametrize('failure',['runtime','scanner-error','unreviewed','empty-findings'])
def test_exception_cannot_turn_runtime_error_scanner_error_or_new_cve_into_success(gate,monkeypatch,failure):
    root,state=enable_exception(gate,monkeypatch)
    if failure=='runtime':state['failure']='runtime'
    if failure=='scanner-error':state['scanner_exit']=1
    if failure=='unreviewed':
        state['finding_data']['Results'][0]['Vulnerabilities'].append({'Severity':'HIGH','VulnerabilityID':'new'})
    if failure=='empty-findings':state['finding_data']['Results'][0]['Vulnerabilities']=[]
    with pytest.raises(g.GateError):execute(root,[NAMES[0]])
    assert json.loads((root/'trivy-reports/21/gate.json').read_text())['status']=='failed'
