#!/usr/bin/env python3
"""EVCOWBBE canonical Django launcher. No Go DB writes, no HAL."""
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

root = Path('/srv/evcowbbe/python-releases').resolve(strict=True)
release = Path('/srv/evcowbbe/current-python').resolve(strict=True)
if release.parent != root or not release.name or len(release.name) != 40 or any(c not in '0123456789abcdef' for c in release.name):
    raise SystemExit('COW_RELEASE_BOUNDARY_FAILED')
if (release / 'REVISION').read_text().strip() != release.name or not (release / 'READY').is_file():
    raise SystemExit('COW_RELEASE_UNSEALED')
original = urlsplit(os.environ.get('DATABASE_URL', ''))
if (original.scheme, original.hostname, original.port, original.username, original.path) != (
        'postgresql', '127.0.0.1', 5432, 'evcowbbe_app', '/evcowbbe'):
    raise SystemExit('COW_DATABASE_BOUNDARY_FAILED')
os.environ['DATABASE_URL'] = urlunsplit(original._replace(path='/evcowbbe_django_next'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['COW_HAL_BASE_URL'] = ''
os.environ['COW_HAL_SHARED_SECRET'] = ''
exe = str(release / '.venv/bin/gunicorn')
os.execv(exe, [exe, '--chdir', str(release / 'source'), '--bind', '127.0.0.1:18280',
               '--workers', '1', '--threads', '2', '--timeout', '45', 'config.wsgi:application'])
