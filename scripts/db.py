#!/usr/bin/env python3
"""Schema-first developer CLI for one Django ORM schema and one migration ledger.

`check`, `generate` and `status` without --test-db NEVER contact a database.
Every database-related operation is confined to the operator-marked disposable
PostgreSQL acceptance database, not to VPS2 or the application's database.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MIGRATION_7 = "0007_rbac_pricing_consequential_parity"
MIGRATION_8 = "0008_zone_commercial_registry"
MIGRATION_9 = "0009_zone_admin_operations"


class Refused(Exception):
    pass


def require(cond: bool, reason: str) -> None:
    if not cond:
        raise Refused(reason)


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    require(p.returncode == 0, "Git preflight failed")
    return p.stdout.strip()


def assert_models() -> None:
    require((ROOT / "cow/models.py").is_file(), "canonical schema absent")
    require(not (ROOT / "cow/models_zone.py").exists(), "second model declaration file present")
    require(not any((ROOT / "cow" / name / "models.py").exists()
                    for name in ("users", "drivers", "admin", "common")),
            "role-local schema forbidden")


def offline_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "APP_ENV": "test",
        "DATABASE_URL": "postgresql://unused:unused@127.0.0.1:1/cow_schema_offline?sslmode=disable",
        "PASSWORD_PEPPER": "test-only-password-pepper-" + "p" * 40,
        "OTP_HMAC_PEPPER": "test-only-otp-pepper-" + "o" * 40,
        "PUBLIC_BASE_URL": "http://127.0.0.1:18280",
        "HTTP_LISTEN_ADDR": "127.0.0.1:18280",
        "SMTP_TLS_MODE": "starttls",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    # Never carry auth to subprocesses that aren't running acceptance.
    for name in ("TEST_DATABASE_URL", "COW_PG_TEST_AUTHORIZATION", "COW_PG_TEST_DATABASE_IDENTITY",
                 "COW_PG_TEST_ROLE", "COW_PG_TEST_HARNESS_READY", "PGPASSWORD", "PGSERVICE",
                 "PGSERVICEFILE", "PGPASSFILE", "PGHOST", "PGHOSTADDR", "PGPORT", "PGUSER",
                 "PGDATABASE", "PGOPTIONS", "PGSSLMODE", "PGTARGETSESSIONATTRS"):
        env.pop(name, None)
    return env


def offline_manage(*args: str) -> None:
    p = subprocess.run([sys.executable, "manage.py", *args], env=offline_env(), cwd=ROOT)
    require(p.returncode == 0, f"Django command failed: {args[0]}")


def offline_model_check() -> None:
    assert_models()
    offline_manage("check")
    offline_manage("makemigrations", "cow", "--check", "--dry-run", "--noinput")
    print("MODEL_MIGRATION_PARITY=PASS")


def status_offline() -> None:
    assert_models()
    files = sorted(p.name for p in (ROOT / "cow/migrations").glob("0*.py"))
    require(files, "migration history missing")
    print("CANONICAL_SCHEMA=cow/models.py")
    print("MIGRATION_LEDGER=cow/migrations")
    print("LAST_MIGRATION_FILE=" + files[-1])
    print("MIGRATION_FILE_COUNT=" + str(len(files)))
    print("DATABASE_CONTACTED=NO")


def generate(name: str) -> None:
    require(git("branch", "--show-current") == "anubhab-work",
            "Migration generation requires the core owner's anubhab-work branch")
    require(re.fullmatch(r"[a-z][a-z0-9_]{2,59}", name) is not None,
            "Migration name must be a bounded lower_snake_case label")
    assert_models()
    # This calls actual Django makemigrations, not a second schema generator.
    offline_manage("makemigrations", "cow", "--noinput", "--name", name)
    offline_model_check()
    print("DJANGO_MAKEMIGRATIONS=PASS")
    print("REVIEW_AND_TEST_GENERATED_MIGRATION_BEFORE_APPLY=REQUIRED")


def verified_test_connection():
    # First-stage URL/authorization check runs BEFORE Django settings/DB access.
    from config.pg_test_database import preflight_environment, verify_observed_database
    from scripts.run_pg_tests import (observe_database, assert_allowed_schemas,
                                     assert_no_cross_schema_dependencies)
    target, identity, role = preflight_environment(dict(os.environ))
    # Replace only process-local environment values with test-scoped values.
    from scripts.run_pg_tests import configure_test_environment
    configure_test_environment(os.environ["TEST_DATABASE_URL"])
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.db import connection
    verify_observed_database(target, identity, role, observe_database(connection))
    assert_allowed_schemas(connection)
    assert_no_cross_schema_dependencies(connection)
    return connection


def expected_state(connection) -> str:
    from scripts.run_pg_tests import (cow_migration_names, MIGRATIONS_0007, MIGRATIONS_0008,
                                      assert_expected_schema)
    actual = cow_migration_names(connection)
    from scripts.run_pg_tests import MIGRATIONS_0009
    if actual == MIGRATIONS_0007:
        name = MIGRATION_7
    elif actual == MIGRATIONS_0008:
        name = MIGRATION_8
    elif actual == MIGRATIONS_0009:
        name = MIGRATION_9
    else:
        raise Refused("Unreviewed test migration ledger; no mutation permitted")
    assert_expected_schema(connection, name)
    return name


def assert_empty_cow_tables(connection) -> None:
    from scripts.run_pg_tests import assert_cow_tables_empty
    assert_cow_tables_empty(connection)


def test_db_command(action: str, *, target: str | None = None, loss_ack: bool = False) -> None:
    connection = verified_test_connection()
    state = expected_state(connection)
    from django.core.management import call_command
    from scripts.run_pg_tests import MIGRATIONS_0007, MIGRATIONS_0008, cow_migration_names
    if action == "status":
        print("APPROVED_DISPOSABLE_DATABASE=PASS")
        print("APPLIED_SCHEMA=" + state)
        return
    if action == "plan":
        print("APPROVED_DISPOSABLE_DATABASE=PASS")
        call_command("migrate", "--plan", interactive=False, verbosity=1)
        return
    if action == "verify":
        offline_model_check()  # separate offline child with no DB access
        print("APPROVED_TEST_DB_CATALOG=PASS")
        print("APPLIED_SCHEMA=" + state)
        return
    if action == "up":
        require(target == MIGRATION_8, "Only reviewed migration 0008 is approved for test apply")
        require(state == MIGRATION_7, "Test DB must be at reviewed 0007 before upgrade")
        assert_empty_cow_tables(connection)
        call_command("migrate", "cow", MIGRATION_8, interactive=False, verbosity=1)
        require(cow_migration_names(connection) == MIGRATIONS_0008, "Migration ledger not updated")
        require(expected_state(connection) == MIGRATION_8, "Schema catalog verification failed")
        print("APPROVED_TEST_DATABASE_UP=PASS")
        return
    if action == "down":
        require(loss_ack, "Reversal requires --ack-data-loss")
        require(target == MIGRATION_7, "Only reviewed 0008->0007 reversal is permitted")
        require(state == MIGRATION_8, "Test DB must be at reviewed 0008 before reversing")
        assert_empty_cow_tables(connection)
        call_command("migrate", "cow", MIGRATION_7, interactive=False, verbosity=1)
        require(cow_migration_names(connection) == MIGRATIONS_0007, "Migration ledger mismatch after reversal")
        require(expected_state(connection) == MIGRATION_7, "Catalog mismatch after reversal")
        print("APPROVED_TEST_DATABASE_DOWN=PASS")
        return
    if action == "rehearse":
        require(loss_ack, "Rehearsal requires --ack-data-loss")
        require(state == MIGRATION_8, "Start reversible rehearsal with reviewed 0008 applied")
        assert_empty_cow_tables(connection)
        # Reverse only empty zone tables, verify 0007, then reapply exact 0008.
        call_command("migrate", "cow", MIGRATION_7, interactive=False, verbosity=1)
        require(cow_migration_names(connection) == MIGRATIONS_0007, "Rehearsal reverse ledger mismatch")
        require(expected_state(connection) == MIGRATION_7, "Rehearsal reverse catalog mismatch")
        call_command("migrate", "cow", MIGRATION_8, interactive=False, verbosity=1)
        require(cow_migration_names(connection) == MIGRATIONS_0008, "Rehearsal reapply ledger mismatch")
        require(expected_state(connection) == MIGRATION_8, "Rehearsal reapply catalog mismatch")
        print("DISPOSABLE_PG_0008_REVERSE_REAPPLY=PASS")
        return
    raise Refused("Unknown test database action")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for c in ("status", "check", "plan", "up", "down", "verify", "rehearse"):
        item = sub.add_parser(c)
        if c == "status": item.add_argument("--test-db", action="store_true")
        if c in ("up", "down"):
            item.add_argument("--to", required=True)
        if c in ("down", "rehearse"):
            item.add_argument("--ack-data-loss", action="store_true")
    create=sub.add_parser("generate")
    create.add_argument("--name", required=True)
    run=sub.add_parser("test")
    run.add_argument("mode",choices=("fresh", "existing", "upgrade-0007-to-0008"))
    args=parser.parse_args()
    if args.command in ("status", "check", "generate") and not getattr(args,"test_db",False):
        if args.command == "status":status_offline()
        elif args.command == "check":offline_model_check()
        else:generate(args.name)
        return
    assert_models()
    if args.command == "test":
        # Delegate to the existing marker-verified runner. It does not reset schemas.
        p=subprocess.run([sys.executable,str(ROOT/"scripts/run_pg_tests.py"),"--"+args.mode],cwd=ROOT)
        require(p.returncode == 0, "PostgreSQL integration acceptance failed")
        print("POSTGRES_0008_ACCEPTANCE=PASS")
        return
    test_db_command(args.command, target=getattr(args,"to",None),
                    loss_ack=getattr(args,"ack_data_loss",False))


if __name__ == "__main__":
    from config.pg_test_database import TestDatabaseError
    try:
        main()
    except (Refused, TestDatabaseError, ValueError) as exc:
        print("DB_WORKFLOW_REFUSED: " + str(exc),file=sys.stderr)
        raise SystemExit(2)
