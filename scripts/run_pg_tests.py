#!/usr/bin/env python3
"""Fail-closed acceptance for the one operator-provisioned PostgreSQL database.

This command never creates, drops, truncates, flushes, or resets a database.
It accepts only ``cow_wp31a_20261030_test`` after identity, ownership,
privilege, schema, dependency, and migration-ledger checks. An operator-only
public-schema reset is documented separately; it preserves the marker schema.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
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

MIGRATIONS_0003 = (
    "0001_initial",
    "0002_business_invariants",
    "0003_admin_fleet_vehicle_parity",
)
MIGRATIONS_0004 = MIGRATIONS_0003 + ("0004_admin_charger_connector_pairing_parity",)
MIGRATIONS_0005 = MIGRATIONS_0004 + ("0005_admin_rbac_pricing_parity",)
MIGRATIONS_0006 = MIGRATIONS_0005 + ("0006_customer_master_parity",)
MIGRATIONS_0007 = MIGRATIONS_0006 + ("0007_rbac_pricing_consequential_parity",)
FRAMEWORK_TABLES = {
    "auth_group",
    "auth_group_permissions",
    "auth_permission",
    "auth_user",
    "auth_user_groups",
    "auth_user_user_permissions",
    "django_content_type",
    "django_migrations",
}
TRIGGERS_0003 = {
    "cow_app_vehicle_plane",
    "cow_app_address_plane",
    "cow_app_request_plane",
    "cow_driver_profile_plane",
    "cow_admin_role_plane",
    "cow_admin_override_plane",
    "cow_job_quote_consistency",
    "cow_quote_immutable",
    "cow_request_snapshot_immutable",
    "cow_pricing_terms_immutable",
    "cow_vehicle_maintenance_no_overlap",
    "cow_vehicle_maintenance_immutable",
}
FUNCTIONS_0003 = {
    "cow_guard_principal_plane",
    "cow_guard_job_quote",
    "cow_protect_quote_truth",
    "cow_protect_request_snapshot",
    "cow_protect_published_pricing",
    "cow_vehicle_maintenance_guard",
    "cow_vehicle_maintenance_append_only",
}
TRIGGERS_0004 = TRIGGERS_0003 | {
    "cow_charger_maintenance_no_overlap",
    "cow_charger_maintenance_immutable",
    "cow_pairing_lifecycle_guard",
}
FUNCTIONS_0004 = FUNCTIONS_0003 | {
    "cow_charger_maintenance_guard",
    "cow_charger_maintenance_append_only",
    "cow_pairing_closed_immutable",
}
TRIGGERS_0005 = TRIGGERS_0004 | {"cow_admin_pricing_revision_guard"}
FUNCTIONS_0005 = FUNCTIONS_0004 | {"cow_admin_pricing_guard"}
TRIGGERS_0006 = TRIGGERS_0005 | {
    "cow_customer_vehicle_guard", "cow_customer_address_guard", "cow_customer_profile_guard",
}
FUNCTIONS_0006 = FUNCTIONS_0005 | {"cow_customer_master_guard", "cow_customer_profile_guard"}
UPGRADE_ACTIVE_PAIRING_ID = uuid.UUID("a1136314-7d21-4a21-8001-000000000001")
UPGRADE_CLOSED_PAIRING_ID = uuid.UUID("a1136314-7d21-4a21-8001-000000000002")
UPGRADE_ACTIVE_FROM = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
UPGRADE_CLOSED_FROM = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
UPGRADE_CLOSED_TO = datetime(2026, 9, 15, 18, 30, tzinfo=UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--fresh",
        action="store_true",
        help="require an empty public schema, then accept fresh 0001 through 0007",
    )
    modes.add_argument(
        "--prepare-upgrade-fixture",
        action="store_true",
        help="prepare a reviewed 0003-only active/closed pairing fixture without a flush",
    )
    modes.add_argument(
        "--upgrade-0003-to-0004",
        action="store_true",
        help="require the reviewed 0003 fixture and rehearse only its upgrade to 0004",
    )
    modes.add_argument(
        "--existing",
        action="store_true",
        help="accept an already-migrated 0007 test schema without migration or reset",
    )
    modes.add_argument(
        "--upgrade-0004-to-0005",
        action="store_true",
        help="guarded additive 0004 to 0005 rehearsal",
    )
    modes.add_argument(
        "--upgrade-0005-to-0006",
        action="store_true",
        help="guarded additive 0005 to 0006 rehearsal",
    )
    modes.add_argument(
        "--upgrade-0006-to-0007", action="store_true",
        help="guarded additive 0006 to 0007 pricing trigger update",
    )
    # Keep a dangerous historical invocation fail-closed rather than silently
    # accepting it. It deliberately has no implementation path.
    modes.add_argument("--reset", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.reset:
        parser.error(
            "--reset is intentionally disabled: Django flush is never a schema reset; "
            "use the documented operator-only public-schema reset, then --fresh"
        )
    if not (
        args.prepare_upgrade_fixture
        or args.upgrade_0003_to_0004
        or args.existing
        or args.upgrade_0004_to_0005
        or args.upgrade_0005_to_0006
        or args.upgrade_0006_to_0007
    ):
        args.fresh = True
    return args


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
                       marker.identity, inet_server_port(),
                       r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolbypassrls,
                       marker.row_count, marker.only_singleton,
                       database_owner.rolname = current_user,
                       marker_schema_owner.rolname = current_user,
                       public_schema_owner.rolname = current_user,
                       has_database_privilege(current_user, current_database(), 'CONNECT'),
                       has_schema_privilege(current_user, 'public', 'USAGE'),
                       has_schema_privilege(current_user, 'public', 'CREATE'),
                       has_schema_privilege(current_user, %s, 'USAGE'),
                       has_table_privilege(current_user, %s, 'SELECT')
                FROM pg_roles AS r
                JOIN pg_database AS database_row ON database_row.datname = current_database()
                JOIN pg_roles AS database_owner ON database_owner.oid = database_row.datdba
                JOIN pg_namespace AS marker_schema ON marker_schema.nspname = %s
                JOIN pg_roles AS marker_schema_owner
                  ON marker_schema_owner.oid = marker_schema.nspowner
                JOIN pg_namespace AS public_schema ON public_schema.nspname = 'public'
                JOIN pg_roles AS public_schema_owner
                  ON public_schema_owner.oid = public_schema.nspowner
                CROSS JOIN LATERAL (
                    SELECT min(identity) AS identity, count(*) AS row_count,
                           bool_and(singleton) AS only_singleton
                    FROM {HARNESS_SCHEMA}.{HARNESS_TABLE}
                ) AS marker
                WHERE r.rolname = current_user
                """,
                [HARNESS_SCHEMA, f"{HARNESS_SCHEMA}.{HARNESS_TABLE}", HARNESS_SCHEMA],
            )
            row = cursor.fetchone()
    except Exception as exc:
        raise TestDatabaseError("cannot read the operator-owned PostgreSQL test marker") from exc
    if row is None:
        raise TestDatabaseError("connected PostgreSQL role is not present in pg_roles")
    return ObservedDatabase(*row)


def assert_allowed_schemas(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT nspname FROM pg_namespace
               WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
                 AND nspname NOT IN ('public', %s) ORDER BY nspname""",
            [HARNESS_SCHEMA],
        )
        unexpected = [row[0] for row in cursor.fetchall()]
    if unexpected:
        raise TestDatabaseError("unexpected non-system schemas are present")


def public_tables(connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT c.relname FROM pg_class AS c
               JOIN pg_namespace AS n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"""
        )
        return {row[0] for row in cursor.fetchall()}


def assert_no_cross_schema_dependencies(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT DISTINCT dependent_ns.nspname, referenced_ns.nspname
               FROM pg_depend AS dependency
               JOIN pg_class AS dependent ON dependent.oid = dependency.objid
               JOIN pg_namespace AS dependent_ns ON dependent_ns.oid = dependent.relnamespace
               JOIN pg_class AS referenced ON referenced.oid = dependency.refobjid
               JOIN pg_namespace AS referenced_ns ON referenced_ns.oid = referenced.relnamespace
               WHERE dependent_ns.nspname = 'public'
                 AND referenced_ns.nspname NOT IN ('public', 'pg_catalog')
                 AND referenced_ns.nspname !~ '^pg_'"""
        )
        dependencies = cursor.fetchall()
    if dependencies:
        raise TestDatabaseError(
            "public application objects have unexpected cross-schema dependencies"
        )


def assert_application_schema_empty(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT c.relname FROM pg_class AS c
               JOIN pg_namespace AS n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relkind NOT IN ('i', 't')"""
        )
        objects = [row[0] for row in cursor.fetchall()]
    if objects:
        raise TestDatabaseError("fresh acceptance requires an empty public application schema")
    if cow_migration_names(connection):
        raise TestDatabaseError("fresh acceptance requires no existing Cow migration history")


def cow_migration_names(connection) -> tuple[str, ...]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.django_migrations')")
        if cursor.fetchone()[0] is None:
            return ()
        cursor.execute("SELECT name FROM django_migrations WHERE app = 'cow' ORDER BY name")
        return tuple(row[0] for row in cursor.fetchall())


def assert_cow_migration_history(connection, expected: tuple[str, ...]) -> None:
    if cow_migration_names(connection) != expected:
        raise TestDatabaseError("Django Cow migration history is not the reviewed expected ledger")


def expected_cow_tables(connection, migration: str) -> set[str]:
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    state = executor.loader.project_state([("cow", migration)])
    return {model._meta.db_table for model in state.apps.get_app_config("cow").get_models()}


def assert_expected_schema(connection, migration: str) -> None:
    expected = expected_cow_tables(connection, migration)
    actual = {table for table in public_tables(connection) if table.startswith("cow_")}
    if actual != expected:
        raise TestDatabaseError(
            f"public Cow tables do not exactly match reviewed {migration} schema"
        )
    if public_tables(connection) - expected - FRAMEWORK_TABLES:
        raise TestDatabaseError("unexpected public application tables are present")
    if migration == "0003_admin_fleet_vehicle_parity":
        expected_triggers, expected_functions = TRIGGERS_0003, FUNCTIONS_0003
    elif migration == "0004_admin_charger_connector_pairing_parity":
        expected_triggers, expected_functions = TRIGGERS_0004, FUNCTIONS_0004
    elif migration == "0005_admin_rbac_pricing_parity":
        expected_triggers, expected_functions = TRIGGERS_0005, FUNCTIONS_0005
    elif migration == "0006_customer_master_parity":
        expected_triggers, expected_functions = TRIGGERS_0006, FUNCTIONS_0006
    else:
        raise TestDatabaseError("unsupported Cow schema assertion target")
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT t.tgname FROM pg_trigger AS t
               JOIN pg_class AS c ON c.oid = t.tgrelid
               JOIN pg_namespace AS n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND NOT t.tgisinternal
                 AND t.tgname LIKE 'cow\\_%%' ESCAPE '\\'"""
        )
        actual_triggers = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """SELECT p.proname FROM pg_proc AS p
               JOIN pg_namespace AS n ON n.oid = p.pronamespace
               WHERE n.nspname = 'public' AND p.proname LIKE 'cow\\_%%' ESCAPE '\\'"""
        )
        actual_functions = {row[0] for row in cursor.fetchall()}
    if actual_triggers != expected_triggers or actual_functions != expected_functions:
        raise TestDatabaseError("Cow trigger/function catalog differs from the reviewed schema")
    assert_no_cross_schema_dependencies(connection)


def migrate_to_cow_0003(connection) -> None:
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    targets = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "cow"]
    executor.migrate(targets + [("cow", "0003_admin_fleet_vehicle_parity")])


def assert_cow_tables_empty(connection) -> None:
    for table in sorted(table for table in public_tables(connection) if table.startswith("cow_")):
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT EXISTS (SELECT 1 FROM {connection.ops.quote_name(table)} LIMIT 1)"
            )
            if cursor.fetchone()[0]:
                raise TestDatabaseError(
                    "upgrade fixture preparation requires empty reviewed Cow tables"
                )


def prepare_upgrade_fixture(connection) -> None:
    """Create 0003-shaped records with only facts available before 0004."""
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    old_apps = executor.loader.project_state([("cow", "0003_admin_fleet_vehicle_parity")]).apps
    vehicle = old_apps.get_model("cow", "ServiceVehicle")
    charger = old_apps.get_model("cow", "Charger")
    pairing = old_apps.get_model("cow", "VehicleChargerPairing")
    active_vehicle = vehicle.objects.create(
        asset_tag="upgrade-active-vehicle", make="Test", model="Active"
    )
    closed_vehicle = vehicle.objects.create(
        asset_tag="upgrade-closed-vehicle", make="Test", model="Closed"
    )
    active_charger = charger.objects.create(
        asset_tag="upgrade-active-charger",
        hal_identity="upgrade-active-hal",
        manufacturer="Test",
        model="Active",
        rated_power_w=7000,
    )
    closed_charger = charger.objects.create(
        asset_tag="upgrade-closed-charger",
        hal_identity="upgrade-closed-hal",
        manufacturer="Test",
        model="Closed",
        rated_power_w=7000,
    )
    pairing.objects.create(
        id=UPGRADE_ACTIVE_PAIRING_ID,
        vehicle_id=active_vehicle.id,
        charger_id=active_charger.id,
        effective_from=UPGRADE_ACTIVE_FROM,
        effective_to=None,
        expected=True,
    )
    pairing.objects.create(
        id=UPGRADE_CLOSED_PAIRING_ID,
        vehicle_id=closed_vehicle.id,
        charger_id=closed_charger.id,
        effective_from=UPGRADE_CLOSED_FROM,
        effective_to=UPGRADE_CLOSED_TO,
        expected=False,
    )


def assert_upgrade_fixture(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT id, effective_from, effective_to, expected, status, created_at, reason, source
               FROM cow_vehiclechargerpairing WHERE id IN (%s, %s) ORDER BY id""",
            [UPGRADE_ACTIVE_PAIRING_ID, UPGRADE_CLOSED_PAIRING_ID],
        )
        rows = cursor.fetchall()
    if len(rows) != 2:
        raise TestDatabaseError("the reviewed 0003 upgrade fixture is missing")
    active, closed = rows
    if (
        active[0] != UPGRADE_ACTIVE_PAIRING_ID
        or active[1] != UPGRADE_ACTIVE_FROM
        or active[2] is not None
        or active[3] is not True
        or active[4:] != ("ACTIVE", None, None, None)
    ):
        raise TestDatabaseError("0004 did not preserve the active pairing identity and truth")
    if (
        closed[0] != UPGRADE_CLOSED_PAIRING_ID
        or closed[1] != UPGRADE_CLOSED_FROM
        or closed[2] != UPGRADE_CLOSED_TO
        or closed[3] is not False
        or closed[4:] != ("ENDED", None, None, None)
    ):
        raise TestDatabaseError("0004 did not preserve the closed pairing identity and truth")


def run_pytest(*paths: str) -> int:
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *paths], cwd=ROOT)


def main() -> int:
    args = parse_args()
    try:
        target, identity, role = preflight_environment(dict(os.environ))
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
        assert_allowed_schemas(connection)
        if args.existing:
            assert_cow_migration_history(connection, MIGRATIONS_0007)
            assert_expected_schema(connection, "0006_customer_master_parity")
            assert_cow_tables_empty(connection)
            return run_pytest("tests/integration")
        if args.upgrade_0006_to_0007:
            assert_cow_migration_history(connection, MIGRATIONS_0006)
            assert_expected_schema(connection, "0006_customer_master_parity")
            assert_cow_tables_empty(connection)
            call_command("migrate", "cow", "0007_rbac_pricing_consequential_parity",
                         interactive=False, verbosity=1)
            assert_cow_migration_history(connection, MIGRATIONS_0007)
            assert_expected_schema(connection, "0006_customer_master_parity")
            return run_pytest("tests/integration")
        if args.upgrade_0005_to_0006:
            assert_cow_migration_history(connection, MIGRATIONS_0005)
            assert_expected_schema(connection, "0005_admin_rbac_pricing_parity")
            assert_cow_tables_empty(connection)
            call_command("migrate", "cow", "0006_customer_master_parity", interactive=False, verbosity=1)
            assert_cow_migration_history(connection, MIGRATIONS_0006)
            assert_expected_schema(connection, "0006_customer_master_parity")
            return run_pytest("tests/integration/test_customer_master.py")
        if args.upgrade_0004_to_0005:
            assert_cow_migration_history(connection, MIGRATIONS_0004)
            assert_expected_schema(connection, "0004_admin_charger_connector_pairing_parity")
            assert_cow_tables_empty(connection)
            call_command(
                "migrate", "cow", "0005_admin_rbac_pricing_parity", interactive=False, verbosity=1
            )
            assert_cow_migration_history(connection, MIGRATIONS_0005)
            assert_expected_schema(connection, "0005_admin_rbac_pricing_parity")
            return run_pytest("tests/integration/test_admin_rbac_pricing.py")
        if args.prepare_upgrade_fixture:
            assert_application_schema_empty(connection)
            migrate_to_cow_0003(connection)
            assert_cow_migration_history(connection, MIGRATIONS_0003)
            assert_expected_schema(connection, "0003_admin_fleet_vehicle_parity")
            assert_cow_tables_empty(connection)
            prepare_upgrade_fixture(connection)
            return 0
        if args.upgrade_0003_to_0004:
            assert_cow_migration_history(connection, MIGRATIONS_0003)
            assert_expected_schema(connection, "0003_admin_fleet_vehicle_parity")
            call_command(
                "migrate",
                "cow",
                "0004_admin_charger_connector_pairing_parity",
                interactive=False,
                verbosity=1,
            )
            assert_cow_migration_history(connection, MIGRATIONS_0004)
            assert_expected_schema(connection, "0004_admin_charger_connector_pairing_parity")
            assert_upgrade_fixture(connection)
            os.environ["COW_PG_EXPECT_UPGRADE_FIXTURE"] = "1"
            return run_pytest("tests/integration/test_pairing_upgrade.py")
        assert_application_schema_empty(connection)
        call_command("migrate", interactive=False, verbosity=1)
        assert_cow_migration_history(connection, MIGRATIONS_0007)
        assert_expected_schema(connection, "0006_customer_master_parity")
        before = cow_migration_names(connection)
        call_command("migrate", interactive=False, verbosity=1)
        if cow_migration_names(connection) != before:
            raise TestDatabaseError("migration replay changed the Cow ledger")
    except TestDatabaseError as exc:
        print(f"POSTGRES_ACCEPTANCE_REFUSED: {exc}", file=sys.stderr)
        return 2
    return run_pytest("tests/integration")


if __name__ == "__main__":
    raise SystemExit(main())
