\set ON_ERROR_STOP on

-- Operator-only recovery for the single disposable acceptance database.
-- Required psql variables: expected_database, expected_marker_identity,
-- expected_owner, test_role, operator_reset_authorization.
-- This file never creates a database and never touches cow_test_harness.

\if :{?expected_database}
\else
\echo 'REFUSED: expected_database is required'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
\if :{?expected_marker_identity}
\else
\echo 'REFUSED: expected_marker_identity is required'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
\if :{?expected_owner}
\else
\echo 'REFUSED: expected_owner is required'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
\if :{?test_role}
\else
\echo 'REFUSED: test_role is required'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
\if :{?operator_reset_authorization}
\else
\echo 'REFUSED: operator_reset_authorization is required'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif

SELECT current_database() = :'expected_database' AS database_matches \gset
\if :database_matches
\else
\echo 'REFUSED: connected database does not match expected_database'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
SELECT :'operator_reset_authorization' = 'RESET_COW_POSTGRES_ACCEPTANCE_SCHEMA_ONLY'
    AS reset_authorized \gset
\if :reset_authorized
\else
\echo 'REFUSED: operator reset authorization did not match'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
SELECT count(*) = 1 AND bool_and(singleton) AND min(identity) = :'expected_marker_identity'
    AS marker_matches
FROM cow_test_harness.database_identity \gset
\if :marker_matches
\else
\echo 'REFUSED: protected marker is missing or does not match'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
SELECT NOT EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
      AND nspname NOT IN ('public', 'cow_test_harness')
) AS schemas_allowed \gset
\if :schemas_allowed
\else
\echo 'REFUSED: unexpected non-system schema exists'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'test_role') AS runner_exists \gset
\if :runner_exists
\else
\echo 'REFUSED: expected test role does not exist'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
SELECT pg_get_userbyid((SELECT nspowner FROM pg_namespace WHERE nspname = 'cow_test_harness'))
       = :'expected_owner' AS marker_owner_matches \gset
\if :marker_owner_matches
\else
\echo 'REFUSED: protected marker schema owner differs from expected_owner'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
SELECT NOT EXISTS (
    SELECT 1
    FROM pg_depend AS dependency
    JOIN pg_class AS dependent ON dependent.oid = dependency.objid
    JOIN pg_namespace AS dependent_ns ON dependent_ns.oid = dependent.relnamespace
    JOIN pg_class AS referenced ON referenced.oid = dependency.refobjid
    JOIN pg_namespace AS referenced_ns ON referenced_ns.oid = referenced.relnamespace
    WHERE dependent_ns.nspname = 'public'
      AND referenced_ns.nspname NOT IN ('public', 'pg_catalog')
      AND referenced_ns.nspname !~ '^pg_'
) AS dependencies_allowed \gset
\if :dependencies_allowed
\else
\echo 'REFUSED: public schema has cross-schema dependencies'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
WITH ledger AS (
    SELECT COALESCE(array_agg(name::text ORDER BY name), ARRAY[]::text[]) AS names
    FROM django_migrations WHERE app = 'cow'
)
SELECT (
    names = ARRAY[
        '0001_initial', '0002_business_invariants',
        '0003_admin_fleet_vehicle_parity'
    ]::text[]
    OR names = ARRAY[
        '0001_initial', '0002_business_invariants',
        '0003_admin_fleet_vehicle_parity',
        '0004_admin_charger_connector_pairing_parity'
    ]::text[]
    OR names = ARRAY[
        '0001_initial', '0002_business_invariants',
        '0003_admin_fleet_vehicle_parity',
        '0004_admin_charger_connector_pairing_parity',
        '0005_admin_rbac_pricing_parity'
    ]::text[]
    OR names = ARRAY[
        '0001_initial', '0002_business_invariants',
        '0003_admin_fleet_vehicle_parity',
        '0004_admin_charger_connector_pairing_parity',
        '0005_admin_rbac_pricing_parity',
        '0006_customer_master_parity'
    ]::text[]
) AS cow_ledger_allowed
FROM ledger \gset
\if :cow_ledger_allowed
\else
\echo 'REFUSED: Cow migration history is neither the reviewed 0003, 0004 nor 0005 ledger'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif
SELECT NOT EXISTS (
    SELECT 1
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND c.relname NOT IN (
          'auth_group', 'auth_group_permissions', 'auth_permission', 'auth_user',
          'auth_user_groups', 'auth_user_user_permissions', 'django_content_type',
          'django_migrations', 'cow_account', 'cow_principal', 'cow_authsession',
          'cow_authratebucket', 'cow_consumedrefreshtoken', 'cow_authchallenge',
          'cow_authactiontoken', 'cow_adminrole', 'cow_adminoverride', 'cow_auditevent',
          'cow_outbox', 'cow_idempotencyreceipt', 'cow_customervehicle',
          'cow_customeraddress', 'cow_customerprofile', 'cow_servicevehicle', 'cow_charger',
          'cow_chargerconnector', 'cow_vehiclechargerpairing', 'cow_driverprofile',
          'cow_dutysession', 'cow_pricingpolicy', 'cow_servicerequest', 'cow_quote',
          'cow_servicejob', 'cow_jobevent', 'cow_chargingsession', 'cow_halcommand',
          'cow_halfact', 'cow_meterobservation', 'cow_vehiclemaintenanceperiod', 'cow_chargermaintenanceperiod'
      )
) AS public_objects_allowed \gset
\if :public_objects_allowed
\else
\echo 'REFUSED: unexpected public application table exists'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif

SELECT NOT EXISTS (
    SELECT 1 FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace
    WHERE n.nspname = 'public'
) AS public_extensions_absent \gset
\if :public_extensions_absent
\else
\echo 'REFUSED: public schema contains a PostgreSQL extension'
DO $cow_guard$ BEGIN RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'; END $cow_guard$;
\endif

BEGIN;
DROP SCHEMA public CASCADE;
CREATE SCHEMA public AUTHORIZATION :"expected_owner";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO :"test_role";
COMMIT;

-- The marker schema/table, its single identity row, and its ownership remain intact.
