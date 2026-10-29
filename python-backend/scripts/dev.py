#!/usr/bin/env python3
"""Local Python dev CLI for the existing EVCOWBBE keyset.

Never performs DDL against the live `evcowbbe` database. The one-time live
replacement is a separate, reviewed operator-approved cutover procedure.
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def env_file():
    path = ROOT / '.env'
    if not path.exists():
        raise SystemExit('Missing ignored .env; copy .env.example and configure privately')
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if '=' not in line or line.lstrip().startswith('export '):
            raise SystemExit('Invalid .env entry')
        k, v = line.split('=', 1)
        if not k.isidentifier():
            raise SystemExit('Invalid .env key')
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def target():
    from config.legacy_env import ConfigError, postgres, listen, public_host, smtp_mode, derive_key
    try:
        d = postgres(os.environ)
        listen(os.environ)
        public_host(os.environ)
        smtp_mode(os.environ)
        derive_key(os.environ, 'doctor-probe')
    except ConfigError as e:
        raise SystemExit(f'Config rejected: {e}') from e
    print(f"TARGET host={d['HOST']} port={d['PORT']} database={d['NAME']}")
    return d['NAME']


def run(*args):
    raise SystemExit(subprocess.call([sys.executable, str(ROOT/'manage.py'), *args], cwd=ROOT))


def main():
    env_file()
    name = target()
    action = sys.argv[1] if len(sys.argv) > 1 else 'help'
    if action == 'doctor':
        print('Read-only configuration validation passed. DB/SMTP NOT contacted.')
        print('Existing peppers: present (values withheld); HAL: disabled unless separately configured')
        return
    if action == 'check': run('check', '--deploy')
    if action == 'schema-check': run('makemigrations', '--check', '--dry-run')
    if action == 'migrate':
        # A disposable *_test database is allowed for developer rehearsal.
        # No way to migrate the current live database through this CLI.
        target_name = sys.argv[3] if len(sys.argv) == 4 and sys.argv[2] == '--confirm' else ''
        if (not name.endswith('_test') or 'evcowbbe' in name.lower() or
            target_name != name or os.environ.get('ALLOW_TEST_DB_MIGRATION') != name):
            raise SystemExit('Migration refused: only a named disposable *_test DB with '
                             'ALLOW_TEST_DB_MIGRATION and matching --confirm is allowed')
        run('migrate', '--noinput')
    if action == 'run':
        from config.legacy_env import listen
        run('runserver', listen(os.environ), '--noreload')
    if action == 'test':
        raise SystemExit(subprocess.call([sys.executable,'-m','pytest','-q'],cwd=ROOT))
    if action == 'export-openapi':
        run('export_openapi_schema','--api','cow.api.api',
            '--output',str(ROOT/'contracts/openapi.generated.json'),'--sorted')
    if action == 'mail-batch': run('mail_worker', '--limit', '20')
    raise SystemExit('Usage: python scripts/dev.py doctor|check|schema-check|migrate|run|test|export-openapi|mail-batch')

if __name__ == '__main__': main()
