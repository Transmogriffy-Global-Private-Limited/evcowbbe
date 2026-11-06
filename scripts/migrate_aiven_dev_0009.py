#!/usr/bin/env python3
"""Apply ONLY reviewed Sprint 7 migration 0009 to the known Aiven dev DB.

No reset, no database creation/deletion, no external deploy or Git change.
"""
from __future__ import annotations

import os
from pathlib import Path

if Path('.env').is_file():
    from scripts.dev import env_file
    env_file()

if os.environ.get('APP_ENV', 'development') not in {'development', 'test'}:
    raise SystemExit('REFUSED: APP_ENV must be development or test')
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'

import django
django.setup()
from django.core.management import call_command
from django.db import connection

EXPECTED = ('tgpl-psql2-tgplin-eaa5.h.aivencloud.com', 15587, 'defaultdb', 'avnadmin')
settings = connection.settings_dict
with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), current_user, inet_server_port()')
    database, role, connected_port = cursor.fetchone()
    observed = (settings['HOST'], int(settings['PORT']), database, role)
    if observed != EXPECTED or connected_port != EXPECTED[1]:
        raise SystemExit('REFUSED: DATABASE_URL does not identify the reviewed Aiven development target')
    cursor.execute("SELECT COUNT(*) FROM django_migrations WHERE app='cow' AND name='0008_zone_commercial_registry'")
    if cursor.fetchone()[0] != 1:
        raise SystemExit('REFUSED: Existing application is not at the reviewed 0008 baseline')

print('DEV_DB_IDENTITY=PASS; TARGET=defaultdb; MIGRATION=0009_zone_admin_operations')
call_command('migrate', 'cow', '0009_zone_admin_operations', interactive=False, verbosity=1)
with connection.cursor() as cursor:
    cursor.execute("SELECT COUNT(*) FROM django_migrations WHERE app='cow' AND name='0009_zone_admin_operations'")
    migrated = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND indexname='cow_zone_single_published_key'")
    indexed = cursor.fetchone()[0]
    cursor.execute("SELECT pg_get_functiondef('public.cow_admin_pricing_guard()'::regprocedure)")
    definition = cursor.fetchone()[0]
if migrated != 1 or indexed != 1 or 'Composite Zone tariff cannot masquerade' not in definition:
    raise SystemExit('SPRINT7_SCHEMA_POSTFLIGHT=FAILED')
print('SPRINT7_DEV_SCHEMA_0009=PASS')
