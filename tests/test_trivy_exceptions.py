"""Exact, expiring risk acceptance must not hide unrelated or fixable findings."""
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('exceptions', ROOT/'scripts/trivy_exceptions.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
POLICY = ROOT/'security/trivy-exceptions.json'


def report(**changes):
    finding = {'VulnerabilityID':'CVE-2026-76642', 'PkgName':'bsdutils',
               'InstalledVersion':'1:2.41.5-0+deb13u1', 'Severity':'HIGH'}
    finding.update(changes)
    return {'Metadata':{'OS':{'Family':'debian','Name':'13.7'}},
            'Results':[{'Class':'os-pkgs','Type':'debian','Vulnerabilities':[finding]}]}


def evaluate(data=None, image='chargeops-ai-gateway', day=date(2026,10,29)):
    return m.evaluate(data or report(), m.load(POLICY), image, today=day)


def test_only_reviewed_exact_unfixed_os_finding_is_accepted():
    result = evaluate()
    assert result['excepted']==1 and result['blocked']==0
    assert result['exceptions'][0]['cve']=='CVE-2026-76642'
    assert result['exceptions'][0]['expires_on']=='2026-11-05'


@pytest.mark.parametrize('change',[
    {'VulnerabilityID':'CVE-2099-1234'}, {'PkgName':'other'},
    {'InstalledVersion':'new-version'}, {'FixedVersion':'patched'}, {'Severity':'CRITICAL'},
])
def test_new_fixed_changed_or_critical_finding_stays_blocked(change):
    assert evaluate(report(**change))['blocked']==1


def test_other_image_and_expired_exception_stay_blocked():
    assert evaluate(image='chargeops-csms')['blocked']==1
    assert evaluate(day=date(2026,11,5))['blocked']==1


@pytest.mark.parametrize('scope',['python','other-os','other-release','config'])
def test_exception_does_not_cross_scanner_or_os_scope(scope):
    data=report()
    if scope=='python':data['Results'][0].update(Class='lang-pkgs',Type='python-pkg')
    if scope=='other-os':data['Metadata']['OS']['Family']='alpine'
    if scope=='other-release':data['Metadata']['OS']['Name']='12.9'
    if scope=='config':
        finding=data['Results'][0].pop('Vulnerabilities')[0]
        data['Results'][0]['Misconfigurations']=[finding]
    assert evaluate(data)['blocked']==1


def test_all_44_reviewed_rows_match_but_still_remain_visible():
    policy=m.load(POLICY)
    data=report(); rows=[]
    for entry in policy['exceptions']:
        for package,version in entry['packages'].items():
            rows.append({'VulnerabilityID':entry['cve'],'PkgName':package,
                         'InstalledVersion':version,'Severity':'HIGH'})
    data['Results'][0]['Vulnerabilities']=rows
    result=evaluate(data)
    assert len(rows)==44 and result['excepted']==44 and result['blocked']==0
    assert len({item['cve'] for item in result['exceptions']})==8
    assert len(data['Results'][0]['Vulnerabilities'])==44


def test_missing_policy_is_strict_and_invalid_policy_fails(tmp_path):
    assert m.evaluate(report(),m.load(tmp_path/'missing'),'chargeops-ai-gateway')['blocked']==1
    path=tmp_path/'invalid';path.write_text('{}')
    with pytest.raises(ValueError):m.load(path)
