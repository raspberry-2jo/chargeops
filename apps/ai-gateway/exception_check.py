"""Build-time removal and runtime verification of reviewed local attack tools.

Package metadata is retained. This mitigates paths; it does not patch Debian CVEs.
"""
import glob
import json
import os
from pathlib import Path
import stat
import sys

PATTERNS = ('/usr/bin/mount', '/usr/bin/umount', '/usr/bin/nsenter',
            '/usr/bin/infocmp', '/usr/bin/perl*', '/usr/bin/homectl',
            '/usr/lib/systemd/systemd-homed', '/usr/libexec/systemd/systemd-homed',
            '/bin/mount', '/bin/umount', '/bin/nsenter', '/bin/infocmp', '/bin/perl*')


def privileged_files():
    def fail(error):
        raise error
    for directory, _, files in os.walk('/usr', onerror=fail, followlinks=False):
        for name in files:
            path=Path(directory)/name
            mode=path.lstat().st_mode
            if stat.S_ISREG(mode) and mode & (stat.S_ISUID | stat.S_ISGID):
                yield path, mode


def harden():
    if os.geteuid()!=0:
        raise RuntimeError('Build hardening requires root')
    removed=[]
    for pattern in PATTERNS:
        for name in glob.glob(pattern):
            path=Path(name)
            if path.is_file() or path.is_symlink():
                path.unlink(); removed.append(name)
    stripped=[]
    for path,mode in privileged_files():
        path.chmod(stat.S_IMODE(mode) & ~(stat.S_ISUID | stat.S_ISGID))
        stripped.append(str(path))
    return {'removed':removed,'suid_sgid_stripped':stripped}


def check():
    if os.geteuid()!=10001:
        raise RuntimeError('Exception acceptance requires runtime UID 10001')
    remaining=[name for pattern in PATTERNS for name in glob.glob(pattern)]
    if remaining:
        raise RuntimeError('Reviewed local attack tools remain: '+str(remaining))
    privileged=[str(path) for path,_ in privileged_files()]
    if privileged:
        raise RuntimeError('SUID/SGID files remain: '+str(privileged))
    from ai_gateway.runtime_check import check as runtime_check
    runtime_check()
    return {'uid':os.geteuid(),'reviewed_tools_absent':True,'suid_sgid_absent':True,
            'runtime_imports_passed':True,'live_kubernetes_verified':False}


if __name__=='__main__':
    if sys.argv[1:]==['--harden']:
        print(json.dumps(harden(),sort_keys=True))
    elif not sys.argv[1:]:
        print(json.dumps(check(),sort_keys=True))
    else:
        raise SystemExit('Usage: python -m ai_gateway.exception_check [--harden]')
