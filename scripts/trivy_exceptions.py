"""Evaluate reviewed Debian findings without modifying the original scan report."""
from datetime import date, datetime, timezone
import hashlib
import json


def load(path):
    if not path.exists():
        return {'schema':1, 'exceptions':[]}
    policy=json.loads(path.read_text(encoding='utf-8'))
    if policy.get('schema')!=1 or not isinstance(policy.get('exceptions'),list):
        raise ValueError('Invalid Trivy exception policy')
    for entry in policy['exceptions']:
        if (not all(entry.get(key) for key in ('cve','os_family','os_release',
                                              'reason','owner','reviewed_on','expires_on','source'))
                or not isinstance(entry.get('images'),list) or not entry['images']
                or any(not isinstance(image,str) or '*' in image for image in entry['images'])
                or not isinstance(entry.get('packages'),dict) or not entry['packages']):
            raise ValueError('Incomplete Trivy exception entry')
        if date.fromisoformat(entry['expires_on'])<=date.fromisoformat(entry['reviewed_on']):
            raise ValueError('Invalid Trivy exception lifetime')
    return policy


def digest(policy):
    return hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()


def evaluate(data, policy, image, today=None):
    today=today or datetime.now(timezone.utc).date()
    os_info=(data.get('Metadata') or {}).get('OS') or {}
    result={'total':0,'blocked':0,'excepted':0,'exceptions':[]}
    for target in data.get('Results') or []:
        for kind in ('Vulnerabilities','Misconfigurations'):
            for finding in target.get(kind) or []:
                if finding.get('Severity') not in ('HIGH','CRITICAL'):
                    continue
                result['total']+=1
                accepted=None
                for entry in policy['exceptions']:
                    if (kind=='Vulnerabilities' and target.get('Class')=='os-pkgs'
                            and target.get('Type')=='debian' and finding.get('Severity')=='HIGH'
                            and not finding.get('FixedVersion')
                            and os_info.get('Family')==entry['os_family']
                            and str(os_info.get('Name','')).split('.')[0]==entry['os_release']
                            and image in entry['images'] and finding.get('VulnerabilityID')==entry['cve']
                            and finding.get('PkgName') in entry['packages']
                            and finding.get('InstalledVersion')==entry['packages'][finding['PkgName']]
                            and date.fromisoformat(entry['reviewed_on'])<=today<date.fromisoformat(entry['expires_on'])):
                        accepted=entry
                        break
                if accepted:
                    result['excepted']+=1
                    result['exceptions'].append({'cve':accepted['cve'],'package':finding['PkgName'],
                        'installed_version':finding['InstalledVersion'],'reason':accepted['reason'],
                        'owner':accepted['owner'],'expires_on':accepted['expires_on']})
                else:
                    result['blocked']+=1
    return result
