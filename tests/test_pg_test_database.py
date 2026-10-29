import unittest

from config.pg_test_database import (
    RESET_AUTHORIZATION,
    TEST_AUTHORIZATION,
    ObservedDatabase,
    TestDatabaseError,
    parse_postgres_url,
    preflight_environment,
    verify_observed_database,
)

IDENTITY = "c0b3d3a92e344b69b7e01c40d4d3a834"


def environment() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://app:password@127.0.0.1:5432/evcowbbe?sslmode=disable",
        "TEST_DATABASE_URL": "postgresql://cow_test_runner:password@127.0.0.1:5432/cow_acceptance_test?sslmode=disable",
        "COW_PG_TEST_AUTHORIZATION": TEST_AUTHORIZATION,
        "COW_PG_TEST_DATABASE_IDENTITY": IDENTITY,
        "COW_PG_TEST_ROLE": "cow_test_runner",
    }


class PostgreSQLAcceptanceHarnessTests(unittest.TestCase):
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
            "postgresql://application:password@127.0.0.1:5432/cow_acceptance_test"
        )
        env["TEST_DATABASE_URL"] = (
            "postgresql://cow_test_runner:password@localhost:5432/cow_acceptance_test"
        )
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env)

    def test_rejects_malformed_or_unapproved_target(self):
        for value in (
            "",
            "sqlite:///tmp/test.sqlite3",
            "postgresql://u:p@127.0.0.1:5432/cow_acceptance",
        ):
            with self.subTest(value=value):
                env = environment()
                env["TEST_DATABASE_URL"] = value
                with self.assertRaises(TestDatabaseError):
                    preflight_environment(env)

    def test_requires_authorization_for_setup_and_reset(self):
        env = environment()
        del env["COW_PG_TEST_AUTHORIZATION"]
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env)
        env = environment()
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env, reset=True)
        env["COW_PG_TEST_RESET_AUTHORIZATION"] = RESET_AUTHORIZATION
        preflight_environment(env, reset=True)

    def test_rejects_a_url_user_that_does_not_match_the_test_role(self):
        env = environment()
        env["COW_PG_TEST_ROLE"] = "different_role"
        with self.assertRaises(TestDatabaseError):
            preflight_environment(env)

    def test_rejects_unexpected_connected_identity_and_privileged_role(self):
        target, identity, role = preflight_environment(environment())
        observed = ObservedDatabase(
            target.database, role, "wrong", 5432, False, False, False, False
        )
        with self.assertRaises(TestDatabaseError):
            verify_observed_database(target, identity, role, observed)

    def test_rejects_an_unexpected_connected_server_port(self):
        target, identity, role = preflight_environment(environment())
        observed = ObservedDatabase(
            target.database, role, identity, 55432, False, False, False, False
        )
        with self.assertRaises(TestDatabaseError):
            verify_observed_database(target, identity, role, observed)
        observed = ObservedDatabase(
            target.database, role, identity, 5432, False, True, False, False
        )
        with self.assertRaises(TestDatabaseError):
            verify_observed_database(target, identity, role, observed)

    def test_safe_retry_keeps_the_same_preprovisioned_target(self):
        target, identity, role = preflight_environment(environment())
        observed = ObservedDatabase(
            target.database, role, identity, 5432, False, False, False, False
        )
        verify_observed_database(target, identity, role, observed)
        self.assertEqual(
            parse_postgres_url(environment()["TEST_DATABASE_URL"], "TEST_DATABASE_URL"), target
        )


if __name__ == "__main__":
    unittest.main()
