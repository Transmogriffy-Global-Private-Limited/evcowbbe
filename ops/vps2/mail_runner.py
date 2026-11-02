#!/usr/bin/env python3
"""Execute one bounded Django outbox batch from the canonical release."""
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
root = Path('/srv/evcowbbe/python-releases').resolve(strict=True)
release = Path('/srv/evcowbbe/current-python').resolve(strict=True)
if release.parent != root or len(release.name) != 40 or (release / 'REVISION').read_text().strip() != release.name:
    raise SystemExit('MAIL_RELEASE_BOUNDARY_FAILED')
original = urlsplit(os.environ.get('DATABASE_URL', ''))
if (original.scheme, original.hostname, original.port, original.username, original.path) != (
        'postgresql', '127.0.0.1', 5432, 'evcowbbe_app', '/evcowbbe'):
    raise SystemExit('MAIL_DATABASE_BOUNDARY_FAILED')
os.environ['DATABASE_URL'] = urlunsplit(original._replace(path='/evcowbbe_django_next'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['COW_HAL_BASE_URL'] = ''
os.environ['COW_HAL_SHARED_SECRET'] = ''
py = str(release / '.venv/bin/python')
os.execv(py, [py, str(release / 'source/manage.py'), 'mail_worker', '--limit', '10'])
