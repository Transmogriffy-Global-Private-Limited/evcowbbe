#!/usr/bin/env python3
"""Root-side ExecCondition: fail closed unless EVCOWBBE Django is committed and active."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/srv/evcowbbe/python-releases').resolve()
MARKER = Path('/var/lib/evcowbbe-rehost/runtime.json')

def verify():
    if os.geteuid() != 0 or MARKER.is_symlink() or not MARKER.is_file():
        return False
    st = MARKER.stat()
    if st.st_uid != 0 or st.st_mode & 0o077:
        return False
    j = json.loads(MARKER.read_text())
    if j.get('backend') != 'django' or j.get('phase') != 'ACTIVE' or j.get('database') != 'evcowbbe_django_next':
        return False
    release = Path('/srv/evcowbbe/current-python').resolve(strict=True)
    if release.parent != ROOT or release.name != j.get('revision'):
        return False
    if subprocess.run(['systemctl','is-active','--quiet','evcowbbe.service'], timeout=8).returncode:
        return False
    loaded = subprocess.run(['systemctl','show','-p','ExecStart','--value','evcowbbe.service'],
                            capture_output=True,text=True,timeout=8,check=True).stdout
    return '/usr/local/libexec/evcowbbe-django-release-launcher' in loaded and '/srv/evcowbbe/current/server' not in loaded

if __name__ == '__main__':
    try:
        good = verify()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        good = False
    print('COW_MAIL_GATE=' + ('PASS' if good else 'SKIP'), flush=True)
    raise SystemExit(0 if good else 1)
