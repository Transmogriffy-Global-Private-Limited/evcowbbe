"""Fail-closed contract for a pre-provisioned PostgreSQL acceptance database.

The marker is intentionally in an operator-owned schema outside the Django
application schema. A test runner may create and flush application objects,
but it cannot create, replace, or modify the marker used to identify the
database as disposable.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

TEST_AUTHORIZATION = "RUN_COW_POSTGRES_ACCEPTANCE_ONLY"
OPERATOR_RESET_AUTHORIZATION = "RESET_COW_POSTGRES_ACCEPTANCE_SCHEMA_ONLY"
APPROVED_TEST_DATABASE = "cow_wp31a_20261030_test"
HARNESS_SCHEMA = "cow_test_harness"
HARNESS_TABLE = "database_identity"


class TestDatabaseError(ValueError):
    """The candidate PostgreSQL target is not safe for acceptance testing."""

    __test__ = False


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    port: int
    database: str
    user: str


@dataclass(frozen=True)
class ObservedDatabase:
    database: str
    role: str
    identity: str | None
    server_port: int
    role_is_superuser: bool
    role_can_createdb: bool
    role_can_createrole: bool
    role_can_bypassrls: bool
    marker_rows: int
    marker_only_singleton: bool
    runner_owns_database: bool
    runner_owns_marker_schema: bool
    runner_owns_public_schema: bool
    can_connect: bool
    can_use_public: bool
    can_create_public: bool
    can_use_marker: bool
    can_select_marker: bool


def parse_postgres_url(raw: str | None, variable: str) -> DatabaseTarget:
    if not raw:
        raise TestDatabaseError(
            f"{variable} is required but its value is intentionally not displayed"
        )
    try:
        parsed = urlsplit(raw)
        port = parsed.port or 5432
    except ValueError as exc:
        raise TestDatabaseError(f"{variable} is not a valid PostgreSQL URL") from exc
    database = unquote(parsed.path.lstrip("/"))
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not database
        or "/" in database
    ):
        raise TestDatabaseError(f"{variable} must be a PostgreSQL URL with one database name")
    return DatabaseTarget(
        host=parsed.hostname.casefold(),
        port=port,
        database=database,
        user=unquote(parsed.username or ""),
    )


def _endpoint(target: DatabaseTarget) -> tuple[str, int]:
    host = target.host
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return host, target.port


def same_database_target(left: DatabaseTarget, right: DatabaseTarget) -> bool:
    return (
        _endpoint(left) == _endpoint(right)
        and left.database.casefold() == right.database.casefold()
    )


def preflight_environment(env: dict[str, str]) -> tuple[DatabaseTarget, str, str]:
    """Validate non-secret operator inputs before Django imports or DB access."""
    test = parse_postgres_url(env.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL")
    application = parse_postgres_url(env.get("DATABASE_URL"), "DATABASE_URL")
    if test.database != APPROVED_TEST_DATABASE:
        raise TestDatabaseError(
            "TEST_DATABASE_URL must target the operator-provisioned "
            "cow_wp31a_20261030_test database exactly"
        )
    if same_database_target(test, application):
        raise TestDatabaseError("TEST_DATABASE_URL resolves to the application database target")
    if env.get("COW_PG_TEST_AUTHORIZATION") != TEST_AUTHORIZATION:
        raise TestDatabaseError("explicit COW_PG_TEST_AUTHORIZATION is required")
    identity = env.get("COW_PG_TEST_DATABASE_IDENTITY", "")
    role = env.get("COW_PG_TEST_ROLE", "")
    if len(identity) < 32 or any(ch.isspace() for ch in identity):
        raise TestDatabaseError(
            "COW_PG_TEST_DATABASE_IDENTITY must be a non-secret 32+ character marker"
        )
    if not role or any(ch.isspace() for ch in role):
        raise TestDatabaseError(
            "COW_PG_TEST_ROLE must identify the pre-provisioned least-privilege role"
        )
    if test.user != role:
        raise TestDatabaseError("TEST_DATABASE_URL user must match COW_PG_TEST_ROLE")
    return test, identity, role


def verify_observed_database(
    target: DatabaseTarget,
    expected_identity: str,
    expected_role: str,
    observed: ObservedDatabase,
) -> None:
    """Bind URL intent to the actual server connection and marker row."""
    if observed.database != target.database:
        raise TestDatabaseError("connected database identity differs from TEST_DATABASE_URL")
    if observed.role != expected_role:
        raise TestDatabaseError("connected role differs from COW_PG_TEST_ROLE")
    if observed.server_port != target.port:
        raise TestDatabaseError("connected PostgreSQL server port differs from TEST_DATABASE_URL")
    if not observed.identity or not hmac.compare_digest(observed.identity, expected_identity):
        raise TestDatabaseError("operator-owned PostgreSQL test identity marker does not match")
    if any(
        (
            observed.role_is_superuser,
            observed.role_can_createdb,
            observed.role_can_createrole,
            observed.role_can_bypassrls,
        )
    ):
        raise TestDatabaseError("test role has forbidden PostgreSQL administrative capability")
    if observed.marker_rows != 1 or not observed.marker_only_singleton:
        raise TestDatabaseError("operator-owned PostgreSQL marker is not exactly one singleton row")
    if any(
        (
            observed.runner_owns_database,
            observed.runner_owns_marker_schema,
            observed.runner_owns_public_schema,
        )
    ):
        raise TestDatabaseError("test role must not own the database or protected schemas")
    if not all(
        (
            observed.can_connect,
            observed.can_use_public,
            observed.can_create_public,
            observed.can_use_marker,
            observed.can_select_marker,
        )
    ):
        raise TestDatabaseError("test role lacks the reviewed least-privilege acceptance grants")
