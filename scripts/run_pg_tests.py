#!/usr/bin/env python3
"""Run acceptance tests against one pre-provisioned disposable PostgreSQL DB.

This command never creates a database. Normal mode accepts only an empty
application schema, migrates the reviewed Django ledger, proves replay, then runs pytest
with pytest-django database creation disabled. ``--reset`` is an explicit
recovery action for the same positively identified test database.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.pg_test_database import (  # noqa: E402
    HARNESS_SCHEMA,
    HARNESS_TABLE,
    ObservedDatabase,
    TestDatabaseError,
    preflight_environment,
    verify_observed_database,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="explicitly flush only the marker-verified acceptance database before retrying",
    )
    return parser.parse_args()


def configure_test_environment(test_url: str) -> None:
    os.environ["DATABASE_URL"] = test_url
    os.environ["APP_ENV"] = "test"
    os.environ["PUBLIC_BASE_URL"] = "http://localhost:18280"
    os.environ["HTTP_LISTEN_ADDR"] = "127.0.0.1:18280"
    os.environ["SMTP_TLS_MODE"] = "starttls"
    # Generated in-process only; never stored or sent to PostgreSQL.
    os.environ["PASSWORD_PEPPER"] = "test-only-password-pepper-" + ("p" * 40)
    os.environ["OTP_HMAC_PEPPER"] = "test-only-otp-pepper-" + ("o" * 40)
    os.environ["COW_PG_TEST_HARNESS_READY"] = "1"


def observe_database(connection) -> ObservedDatabase:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT current_database(), current_user,
                       (SELECT identity FROM {HARNESS_SCHEMA}.{HARNESS_TABLE} WHERE singleton),
                       inet_server_port(),
                       r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolbypassrls
                FROM pg_roles AS r
                WHERE r.rolname = current_user
                """
            )
            row = cursor.fetchone()
    except Exception as exc:
        raise TestDatabaseError("cannot read the operator-owned PostgreSQL test marker") from exc
    if row is None:
        raise TestDatabaseError("connected PostgreSQL role is not present in pg_roles")
    return ObservedDatabase(*row)


def assert_application_schema_empty(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p')
            ORDER BY c.relname
            """
        )
        tables = [row[0] for row in cursor.fetchall()]
    if tables:
        raise TestDatabaseError(
            "fresh acceptance requires a newly provisioned database with no "
            "public application tables"
        )


def cow_migration_names(connection) -> tuple[str, ...]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.django_migrations')")
        if cursor.fetchone()[0] is None:
            return ()
        cursor.execute("SELECT name FROM django_migrations WHERE app = 'cow' ORDER BY name")
        return tuple(row[0] for row in cursor.fetchall())


def assert_migration_history(connection) -> None:
    if cow_migration_names(connection) != (
        "0001_initial",
        "0002_business_invariants",
        "0003_admin_fleet_vehicle_parity",
        "0004_admin_charger_connector_pairing_parity",
    ):
        raise TestDatabaseError("Django migration history is not exactly the reviewed Cow ledger")


def main() -> int:
    args = parse_args()
    try:
        target, identity, role = preflight_environment(dict(os.environ), reset=args.reset)
    except TestDatabaseError as exc:
        print(f"POSTGRES_ACCEPTANCE_REFUSED: {exc}", file=sys.stderr)
        return 2

    configure_test_environment(os.environ["TEST_DATABASE_URL"])
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.core.management import call_command
    from django.db import connection

    try:
        verify_observed_database(target, identity, role, observe_database(connection))
        if args.reset:
            # Destructive only within the marker-verified DB, with a second
            # explicit authorization token. It preserves migration history.
            call_command("flush", interactive=False, verbosity=1)
        else:
            assert_application_schema_empty(connection)
        call_command("migrate", interactive=False, verbosity=1)
        assert_migration_history(connection)
        before = cow_migration_names(connection)
        call_command("migrate", interactive=False, verbosity=1)
        if cow_migration_names(connection) != before:
            raise TestDatabaseError("migration replay changed the Cow ledger")
    except TestDatabaseError as exc:
        print(f"POSTGRES_ACCEPTANCE_REFUSED: {exc}", file=sys.stderr)
        return 2

    return subprocess.call([sys.executable, "-m", "pytest", "-q", "tests/integration"], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
