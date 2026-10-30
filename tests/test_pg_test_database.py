import unittest
from unittest.mock import patch

from config.pg_test_database import (
    APPROVED_TEST_DATABASE,
    TEST_AUTHORIZATION,
    ObservedDatabase,
    TestDatabaseError,
    parse_postgres_url,
    preflight_environment,
    verify_observed_database,
)
from scripts.run_pg_tests import parse_args

IDENTITY = "c0b3d3a92e344b69b7e01c40d4d3a834"


def environment() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://app:password@127.0.0.1:5432/evcowbbe?sslmode=disable",
        "TEST_DATABASE_URL": (
            "postgresql://cow_test_runner:password@127.0.0.1:5432/"
            f"{APPROVED_TEST_DATABASE}?sslmode=disable"
        ),
        "COW_PG_TEST_AUTHORIZATION": TEST_AUTHORIZATION,
        "COW_PG_TEST_DATABASE_IDENTITY": IDENTITY,
        "COW_PG_TEST_ROLE": "cow_test_runner",
    }


def observed_database(target, role: str, identity: str, **overrides) -> ObservedDatabase:
    values = {
        "database": target.database,
        "role": role,
        "identity": identity,
        "server_port": 5432,
        "role_is_superuser": False,
        "role_can_createdb": False,
        "role_can_createrole": False,
        "role_can_bypassrls": False,
        "marker_rows": 1,
        "marker_only_singleton": True,
        "runner_owns_database": False,
        "runner_owns_marker_schema": False,
        "runner_owns_public_schema": False,
        "can_connect": True,
        "can_use_public": True,
        "can_create_public": True,
        "can_use_marker": True,
        "can_select_marker": True,
    }
    values.update(overrides)
    return ObservedDatabase(**values)


class PostgreSQLAcceptanceHarnessTests(unittest.TestCase):
    def test_default_runner_mode_is_fresh_and_reset_flag_is_refused(self):
        with patch("sys.argv", ["run_pg_tests.py"]):
            self.assertTrue(parse_args().fresh)
        with patch("sys.argv", ["run_pg_tests.py", "--reset"]):
            with self.assertRaises(SystemExit):
                parse_args()

    def test_existing_mode_does_not_imply_fresh_or_reset(self):
        with patch("sys.argv", ["run_pg_tests.py", "--existing"]):
            args = parse_args()
        self.assertTrue(args.existing)
        self.assertFalse(args.fresh)
        with patch("sys.argv", ["run_pg_tests.py", "--existing", "--fresh"]):
            with self.assertRaises(SystemExit):
                parse_args()

    def test_rejects_live_database_target_even_with_different_url_text(self):
        env = environment()
        env["TEST_DATABASE_URL"] = "postgresql://other:password@localhost:5432/evcowbbe"
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env)

    def test_rejects_equal_application_and_test_targets(self):
        env = environment()
        env["TEST_DATABASE_URL"] = env["DATABASE_URL"]
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env)

    def test_rejects_same_approved_database_through_different_url_text(self):
        env = environment()
        env["DATABASE_URL"] = (
            f"postgresql://application:password@127.0.0.1:5432/{APPROVED_TEST_DATABASE}"
        )
        env["TEST_DATABASE_URL"] = (
            f"postgresql://cow_test_runner:password@localhost:5432/{APPROVED_TEST_DATABASE}"
        )
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env)

    def test_rejects_malformed_or_unapproved_target(self):
        for value in (
            "",
            "sqlite:///tmp/test.sqlite3",
            "postgresql://u:p@127.0.0.1:5432/another_test",
        ):
            with self.subTest(value=value):
                env = environment()
                env["TEST_DATABASE_URL"] = value
                with self.assertRaises(TestDatabaseError):
                    preflight_environment(env)

    def test_requires_authorization_for_setup(self):
        env = environment()
        del env["COW_PG_TEST_AUTHORIZATION"]
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env)

    def test_rejects_a_url_user_that_does_not_match_the_test_role(self):
        env = environment()
        env["COW_PG_TEST_ROLE"] = "different_role"
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env)

    def test_rejects_unexpected_connected_identity_and_privileged_role(self):
        target, identity, role = preflight_environment(environment())
        observed = observed_database(target, role, "wrong")
        with self.assertRaises(TestDatabaseError):
            verify_observed_database(target, identity, role, observed)

    def test_rejects_an_unexpected_connected_server_port(self):
        target, identity, role = preflight_environment(environment())
        observed = observed_database(target, role, identity, server_port=55432)
        with self.assertRaises(TestDatabaseError):
            verify_observed_database(target, identity, role, observed)

    def test_rejects_marker_shape_ownership_or_missing_grants(self):
        target, identity, role = preflight_environment(environment())
        for override in (
            {"marker_rows": 2},
            {"marker_only_singleton": False},
            {"runner_owns_database": True},
            {"runner_owns_marker_schema": True},
            {"runner_owns_public_schema": True},
            {"can_select_marker": False},
        ):
            with self.subTest(override=override), self.assertRaises(TestDatabaseError):
                observed = observed_database(target, role, identity, **override)
                verify_observed_database(target, identity, role, observed)
        observed = observed_database(target, role, identity, role_can_createdb=True)
        with self.assertRaises(TestDatabaseError):
            verify_observed_database(target, identity, role, observed)

    def test_safe_retry_keeps_the_same_preprovisioned_target(self):
        target, identity, role = preflight_environment(environment())
        observed = observed_database(target, role, identity)
        verify_observed_database(target, identity, role, observed)
        self.assertEqual(
            parse_postgres_url(environment()["TEST_DATABASE_URL"], "TEST_DATABASE_URL"), target
        )


if __name__ == "__main__":
    unittest.main()
