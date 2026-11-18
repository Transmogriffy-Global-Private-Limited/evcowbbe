#!/usr/bin/env python3
"""Django 5.2 migration-model parity, without any database connection.

This deliberately uses Django's own MigrationLoader(None) and migration
Autodetector, not a hand-built AST proxy. It does NOT check *applied* database
history or the physical PostgreSQL schema; those require isolated acceptance.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

    # Fail hard if Django unexpectedly attempts any database connection while
    # loading the project, migration graph, or model states. No DB I/O is needed.
    from django.db.backends.base.base import BaseDatabaseWrapper

    def no_database_connection(self):
        raise RuntimeError('OFFLINE_PARITY_DB_ACCESS_REFUSED')

    BaseDatabaseWrapper.ensure_connection = no_database_connection

    import django
    django.setup()

    from django.apps import apps
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
    from django.db.migrations.state import ProjectState

    loader = MigrationLoader(None, ignore_no_migrations=True)
    detector = MigrationAutodetector(
        loader.project_state(),
        ProjectState.from_apps(apps),
        questioner=NonInteractiveMigrationQuestioner(specified_apps={'cow'}, dry_run=True),
    )
    changes = detector.changes(graph=loader.graph, trim_to_apps={'cow'})
    if changes:
        print('MODEL_MIGRATION_PARITY=FAIL; DATABASE_CONTACTED=NO', file=sys.stderr)
        for app_label, migrations in sorted(changes.items()):
            for migration in migrations:
                print(f'REQUIRED_MIGRATION={app_label}.{migration.name}', file=sys.stderr)
                for op in migration.operations:
                    print(f'  {op.describe()}', file=sys.stderr)
        return 2

    print('MODEL_MIGRATION_PARITY=PASS; DATABASE_CONTACTED=NO')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('OFFLINE_MIGRATION_PARITY_ERROR: ' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        raise SystemExit(3)
