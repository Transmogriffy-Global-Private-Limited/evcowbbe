"""pytest-django safety override for the pre-provisioned PostgreSQL harness."""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if any(item.get_closest_marker("postgres") for item in items):
        if os.environ.get("COW_PG_TEST_HARNESS_READY") != "1":
            raise pytest.UsageError(
                "PostgreSQL integration tests must run through scripts/run_pg_tests.py"
            )


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """Forbid pytest-django from suffixing or redirecting the approved DB."""


@pytest.fixture(scope="session")
def django_db_setup():
    """Never create, drop, or migrate a pytest-generated test_<database> DB.

    scripts/run_pg_tests.py verifies the marker and applies/replays migrations
    before pytest starts, so the normal setup fixture is intentionally no-op.
    """
