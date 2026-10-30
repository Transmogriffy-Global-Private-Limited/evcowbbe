"""Catalog and migration replay checks for the marker-verified PostgreSQL DB."""

from io import StringIO

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import connection

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]

EXPECTED_CONSTRAINTS = {
    "cow_email_lower",
    "cow_principal_per_plane",
    "cow_one_role_assignment",
    "cow_one_capability_override",
    "cow_mutation_once",
    "cow_one_active_default_address",
    "cow_connector_number_unique",
    "cow_one_current_vehicle_per_charger",
    "cow_one_active_driver_duty",
    "cow_one_active_vehicle_claim",
    "cow_one_active_charger_claim",
    "cow_pricing_revision_unique",
    "cow_admin_pricing_revision_unique",
    "cow_admin_pricing_window",
    "cow_one_active_pricing_policy",
    "cow_one_accepted_quote_per_request",
    "cow_exact_hal_transaction_unique",
    "cow_one_active_session_per_connector",
    "cow_vehicle_retired_inactive",
    "cow_vehicle_maintenance_range",
    "cow_charger_retired_inactive",
    "cow_connector_has_current",
    "cow_pairing_time_range",
    "cow_pairing_lifecycle",
    "cow_charger_maintenance_range",
}
EXPECTED_TRIGGERS = {
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
    "cow_charger_maintenance_no_overlap",
    "cow_charger_maintenance_immutable",
    "cow_pairing_lifecycle_guard",
    "cow_admin_pricing_revision_guard",
    "cow_customer_vehicle_guard",
    "cow_customer_address_guard",
    "cow_customer_profile_guard",
}
EXPECTED_FUNCTIONS = {
    "cow_guard_principal_plane",
    "cow_guard_job_quote",
    "cow_protect_quote_truth",
    "cow_protect_request_snapshot",
    "cow_protect_published_pricing",
    "cow_vehicle_maintenance_guard",
    "cow_vehicle_maintenance_append_only",
    "cow_charger_maintenance_guard",
    "cow_charger_maintenance_append_only",
    "cow_pairing_closed_immutable",
    "cow_admin_pricing_guard",
    "cow_customer_master_guard",
    "cow_customer_profile_guard",
}
EXPECTED_PARTIAL_UNIQUE_INDEXES = {
    "cow_one_active_default_address",
    "cow_one_current_vehicle_per_charger",
    "cow_one_active_driver_duty",
    "cow_one_active_vehicle_claim",
    "cow_one_active_charger_claim",
    "cow_one_active_pricing_policy",
    "cow_one_accepted_quote_per_request",
    "cow_one_active_session_per_connector",
    "cow_one_active_pairing_per_charger",
    "cow_admin_pricing_revision_unique",
}
EXPECTED_PG_CONSTRAINTS = EXPECTED_CONSTRAINTS - EXPECTED_PARTIAL_UNIQUE_INDEXES
EXPECTED_INDEXES = (
    EXPECTED_PG_CONSTRAINTS
    - {
        "cow_email_lower",
        "cow_vehicle_retired_inactive",
        "cow_vehicle_maintenance_range",
        "cow_charger_retired_inactive",
        "cow_connector_has_current",
        "cow_pairing_time_range",
        "cow_pairing_lifecycle",
        "cow_charger_maintenance_range",
        "cow_admin_pricing_window",
    }
    | EXPECTED_PARTIAL_UNIQUE_INDEXES
    | {"cow_vehicle_maint_window", "cow_charger_maint_window"}
)


def test_postgresql_catalog_shape_is_complete_and_has_no_cow_sequences():
    models = tuple(apps.get_app_config("cow").get_models())
    expected_tables = {
        model._meta.db_table: {field.column for field in model._meta.local_fields}
        for model in models
    }
    expected_foreign_keys = {
        (
            model._meta.db_table,
            field.column,
            field.related_model._meta.db_table,
            field.target_field.column,
        )
        for model in models
        for field in model._meta.local_fields
        if field.is_relation and (field.many_to_one or field.one_to_one) and field.db_constraint
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT table_name, column_name FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name LIKE 'cow\\_%' ESCAPE '\\'
               ORDER BY table_name, column_name"""
        )
        actual_tables = {}
        for table_name, column_name in cursor.fetchall():
            actual_tables.setdefault(table_name, set()).add(column_name)
        cursor.execute(
            """SELECT conname FROM pg_constraint AS c
               JOIN pg_namespace AS n ON n.oid = c.connamespace
               WHERE n.nspname = 'public' AND conname = ANY(%s)""",
            [sorted(EXPECTED_PG_CONSTRAINTS)],
        )
        constraints = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """SELECT indexname FROM pg_indexes
               WHERE schemaname = 'public' AND indexname = ANY(%s)""",
            [sorted(EXPECTED_INDEXES)],
        )
        indexes = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """SELECT idx.relname, ix.indisunique, ix.indpred IS NOT NULL
               FROM pg_index AS ix
               JOIN pg_class AS idx ON idx.oid = ix.indexrelid
               JOIN pg_namespace AS ns ON ns.oid = idx.relnamespace
               WHERE ns.nspname = 'public'
                 AND idx.relname = ANY(%s)""",
            [sorted(EXPECTED_PARTIAL_UNIQUE_INDEXES)],
        )
        partial_unique_indexes = {
            name for name, unique, partial in cursor.fetchall() if unique and partial
        }
        cursor.execute(
            """SELECT tgname FROM pg_trigger AS t
               JOIN pg_class AS c ON c.oid = t.tgrelid
               JOIN pg_namespace AS n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND NOT t.tgisinternal AND tgname = ANY(%s)""",
            [sorted(EXPECTED_TRIGGERS)],
        )
        triggers = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """SELECT p.proname FROM pg_proc AS p
               JOIN pg_namespace AS n ON n.oid = p.pronamespace
               WHERE n.nspname = 'public' AND p.proname = ANY(%s)""",
            [sorted(EXPECTED_FUNCTIONS)],
        )
        functions = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """SELECT source_table.relname, source_column.attname,
                      target_table.relname, target_column.attname
               FROM pg_constraint AS fk_constraint
               JOIN pg_class AS source_table ON source_table.oid = fk_constraint.conrelid
               JOIN pg_namespace AS source_schema ON source_schema.oid = source_table.relnamespace
               JOIN pg_class AS target_table ON target_table.oid = fk_constraint.confrelid
               JOIN LATERAL unnest(fk_constraint.conkey) WITH ORDINALITY
                    AS source_key(attnum, position) ON true
               JOIN LATERAL unnest(fk_constraint.confkey) WITH ORDINALITY
                    AS target_key(attnum, position) ON target_key.position = source_key.position
               JOIN pg_attribute AS source_column
                    ON source_column.attrelid = source_table.oid
                   AND source_column.attnum = source_key.attnum
               JOIN pg_attribute AS target_column
                    ON target_column.attrelid = target_table.oid
                   AND target_column.attnum = target_key.attnum
               WHERE fk_constraint.contype = 'f'
                 AND source_schema.nspname = 'public'
                 AND source_table.relname LIKE 'cow\\_%' ESCAPE '\\'"""
        )
        foreign_keys = set(cursor.fetchall())
        cursor.execute(
            "SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' "
            "AND sequencename LIKE 'cow\\_%' ESCAPE '\\'"
        )
        sequences = {row[0] for row in cursor.fetchall()}
    assert actual_tables == expected_tables
    assert EXPECTED_PG_CONSTRAINTS <= constraints
    assert EXPECTED_INDEXES <= indexes
    assert EXPECTED_PARTIAL_UNIQUE_INDEXES <= partial_unique_indexes
    assert foreign_keys == expected_foreign_keys
    assert EXPECTED_TRIGGERS <= triggers
    assert EXPECTED_FUNCTIONS <= functions
    # Cow application primary keys are UUIDs; serial-sequence drift is invalid.
    assert sequences == set()


def test_cow_migration_history_and_replay_are_stable():
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM django_migrations WHERE app = 'cow' ORDER BY name")
        before = tuple(row[0] for row in cursor.fetchall())
    output = StringIO()
    call_command("migrate", interactive=False, stdout=output, verbosity=1)
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM django_migrations WHERE app = 'cow' ORDER BY name")
        after = tuple(row[0] for row in cursor.fetchall())
    assert before == (
        "0001_initial",
        "0002_business_invariants",
        "0003_admin_fleet_vehicle_parity",
        "0004_admin_charger_connector_pairing_parity",
        "0005_admin_rbac_pricing_parity",
        "0006_customer_master_parity",
    )
    assert after == before
