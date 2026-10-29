#!/usr/bin/env python3
"""Integration test gate: only an explicitly configured disposable *_test DB."""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

u = urlsplit(os.environ.get('TEST_DATABASE_URL', ''))
name = unquote(u.path.strip('/'))
if (u.scheme not in {'postgresql','postgres'} or not u.hostname or
    not name.endswith('_test') or any(k in name.lower() for k in ('evcowbbe','prod','live'))):
    raise SystemExit('Refusing integration tests: TEST_DATABASE_URL must target an isolated *_test PostgreSQL DB')
if os.environ.get('TEST_DATABASE_URL') == os.environ.get('DATABASE_URL'):
    raise SystemExit('TEST_DATABASE_URL must not equal application DATABASE_URL')
os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
os.environ['APP_ENV'] = 'test'
os.environ['PUBLIC_BASE_URL'] = 'http://localhost:18280'
os.environ['HTTP_LISTEN_ADDR'] = '127.0.0.1:18280'
os.environ['SMTP_TLS_MODE'] = 'starttls'
# Generated in-process only, never stored or used against a real app DB.
os.environ['PASSWORD_PEPPER'] = 'test-only-password-pepper-' + ('p'*40)
os.environ['OTP_HMAC_PEPPER'] = 'test-only-otp-pepper-' + ('o'*40)
raise SystemExit(subprocess.call([sys.executable,'-m','pytest','-q','tests/integration'], cwd=Path(__file__).parents[1]))
