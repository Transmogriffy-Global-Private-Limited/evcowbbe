"""SQLite-backed, local-only deployment control-plane state.

This module deliberately has no capability to build, fetch, deploy, restart,
contact a network service, read application configuration, or execute a
migration. It only records durable control-plane intent and confirmed state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar


SCHEMA_VERSION = 4
BUSY_TIMEOUT_MS = 5_000
MAX_ACTOR_LENGTH = 128
MAX_SOURCE_ID_LENGTH = 192
MAX_REASON_LENGTH = 1_024
MAX_ERROR_LENGTH = 2_048
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PLAN_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$")
REQUEST_FIELDS = frozenset({"event", "sha", "branch", "actor", "source_id"})

RUN_TRANSITIONS = {
    "planned": {"superseded", "canceled"},
    "running": {"succeeded", "failed", "canceled"},
    "migration_boundary_claimed": {"migration_uncertain"},
    "migration_uncertain": {"failed"},
    "migration_committed": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
    "superseded": set(),
    "canceled": set(),
}
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "superseded", "canceled"})


class ControlPlaneError(Exception):
    """Base error for a safe, local control-plane operation."""

    exit_code = 5


class ValidationError(ControlPlaneError):
    exit_code = 2


class IdempotencyConflict(ControlPlaneError):
    exit_code = 3


class InvalidTransition(ControlPlaneError):
    exit_code = 4


class ControlConflict(InvalidTransition):
    """A durable operator or execution ownership conflict."""


class ExecutionBlocked(InvalidTransition):
    """A current safety fence blocks work before an irreversible boundary."""


class MigrationBoundaryActive(InvalidTransition):
    """The database migration may have crossed its irreversible boundary."""


class StateIntegrityError(ControlPlaneError):
    exit_code = 5


class UnsupportedSchemaVersion(StateIntegrityError):
    pass


T = TypeVar("T")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _new_id() -> str:
    return uuid.uuid4().hex


class ControlPlane:
    """The single authoritative durable store for Burner 1 control state."""

    def __init__(self, state_db: str | os.PathLike[str]) -> None:
        self.state_db = str(state_db)
        if not self.state_db or self.state_db == ":memory:" or "\x00" in self.state_db:
            raise ValidationError("a durable state database path is required")

    def initialize(self) -> None:
        """Create or validate schema without resetting any existing state."""
        try:
            Path(self.state_db).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateIntegrityError(f"cannot prepare SQLite control-plane state path: {exc}") from exc

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            meta_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'control_plane_meta'"
            ).fetchone()
            if not meta_exists:
                self._create_schema(connection)
                connection.execute(
                    "INSERT INTO control_plane_meta (singleton, schema_version, initialized_at) VALUES (1, ?, ?)",
                    (SCHEMA_VERSION, utc_now()),
                )
            else:
                rows = connection.execute(
                    "SELECT schema_version FROM control_plane_meta WHERE singleton = 1"
                ).fetchall()
                if len(rows) != 1:
                    raise StateIntegrityError("control-plane metadata is inconsistent")
                version = int(rows[0]["schema_version"])
                if version > SCHEMA_VERSION:
                    raise UnsupportedSchemaVersion(
                        f"control-plane schema {version} is newer than supported schema {SCHEMA_VERSION}"
                    )
                if version != SCHEMA_VERSION:
                    raise UnsupportedSchemaVersion(
                        f"control-plane schema {version} is not supported by this implementation"
                    )
                self._require_schema_tables(connection)
            foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_violations:
                raise StateIntegrityError("SQLite foreign-key integrity check failed")
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise StateIntegrityError(f"SQLite integrity check failed: {integrity}")
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise StateIntegrityError(f"SQLite state is unavailable or corrupt: {exc}") from exc
        finally:
            connection.close()

    def pragma_settings(self) -> dict[str, Any]:
        self.initialize()
        connection = self._connect()
        try:
            return {
                "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "foreign_keys": int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                "busy_timeout": int(connection.execute("PRAGMA busy_timeout").fetchone()[0]),
            }
        finally:
            connection.close()

    def submit_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        validated, serialized, payload_hash = self._validate_request(payload)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            existing = connection.execute(
                "SELECT * FROM deployment_requests WHERE source_id = ?", (validated["source_id"],)
            ).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise IdempotencyConflict(
                        f"source_id {validated['source_id']!r} was already used for different request content"
                    )
                return {"created": False, "request": self._request_dict(existing)}

            request_id = _new_id()
            accepted_at = utc_now()
            connection.execute(
                """
                INSERT INTO deployment_requests
                    (id, event, sha, branch, actor, source_id, canonical_payload, payload_hash, status, accepted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)
                """,
                (
                    request_id,
                    validated["event"],
                    validated["sha"],
                    validated["branch"],
                    validated["actor"],
                    validated["source_id"],
                    serialized,
                    payload_hash,
                    accepted_at,
                ),
            )
            self._append_event(
                connection,
                "request_accepted",
                "deployment_request",
                request_id,
                {"payload_hash": payload_hash, "sha": validated["sha"], "source_id": validated["source_id"]},
            )
            row = connection.execute("SELECT * FROM deployment_requests WHERE id = ?", (request_id,)).fetchone()
            return {"created": True, "request": self._request_dict(row)}

        return self._transaction(operation)

    def list_requests(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = self._validate_limit(limit)
        return self._read_rows(
            "SELECT * FROM deployment_requests ORDER BY sequence DESC LIMIT ?", (limit,), self._request_dict
        )

    def desired_state(self, trusted_prod_sha: str | None = None) -> dict[str, Any]:
        """Inspect selection using only an externally verified prod SHA.

        Request arrival order is deliberately never used as remote-branch
        authority. `trusted_prod_sha` is an input from a future trusted
        executor, not a value this local-only package can verify.
        """
        if trusted_prod_sha is not None:
            trusted_prod_sha = self._validate_sha(trusted_prod_sha)
        return self._read(lambda connection: self._desired_state(connection, trusted_prod_sha))

    def create_run(
        self, request_id: str, trusted_prod_sha: str, actor: str = "reconciler"
    ) -> dict[str, Any]:
        request_id = self._validate_identifier("request_id", request_id, 64)
        trusted_prod_sha = self._validate_sha(trusted_prod_sha)
        actor = self._validate_identifier("actor", actor, MAX_ACTOR_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            request = connection.execute(
                "SELECT * FROM deployment_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if not request:
                raise ValidationError(f"unknown deployment request {request_id}")
            desired = self._desired_state(connection, trusted_prod_sha)
            if desired["state"] != "ready" or desired["request"]["id"] != request_id:
                raise ExecutionBlocked("request is not the exact externally verified, eligible ingress candidate")
            prior_sha_run = connection.execute(
                "SELECT * FROM deployment_runs WHERE sha = ? ORDER BY attempt DESC LIMIT 1", (request["sha"],)
            ).fetchone()
            retry_control = None
            if prior_sha_run and self._run_requires_operator_retry(prior_sha_run):
                retry_control = connection.execute(
                    """
                    SELECT * FROM operator_controls
                    WHERE control_type = 'retry' AND target_sha = ? AND status = 'active'
                    LIMIT 1
                    """,
                    (request["sha"],),
                ).fetchone()
                if not retry_control:
                    raise InvalidTransition(
                        "this SHA already has deployment work; an explicit unused retry authorization is required"
                    )
            active = connection.execute(
                """
                SELECT * FROM deployment_runs
                WHERE status IN ('planned', 'running', 'migration_boundary_claimed', 'migration_uncertain', 'migration_committed')
                LIMIT 1
                """
            ).fetchone()
            if active:
                if active["status"] == "planned" and active["sha"] != trusted_prod_sha:
                    self._finish_run(
                        connection,
                        active,
                        "superseded",
                        "authoritative_prod_head_changed",
                        {"code": "AUTHORITATIVE_HEAD_CHANGED", "new_sha": trusted_prod_sha},
                    )
                else:
                    raise ControlConflict(f"active deployment run {active['id']} owns execution")
            run_id = _new_id()
            now = utc_now()
            attempt = int(
                connection.execute("SELECT count(*) FROM deployment_runs WHERE sha = ?", (request["sha"],)).fetchone()[0]
            ) + 1
            connection.execute(
                """
                INSERT INTO deployment_runs (id, request_id, sha, attempt, status, created_at, updated_at, actor)
                VALUES (?, ?, ?, ?, 'planned', ?, ?, ?)
                """,
                (run_id, request_id, request["sha"], attempt, now, now, actor),
            )
            if retry_control:
                connection.execute(
                    """
                    UPDATE operator_controls
                    SET status = 'consumed', consumed_by = ?, consumed_at = ?, consumed_run_id = ?
                    WHERE id = ?
                    """,
                    (actor, now, run_id, retry_control["id"]),
                )
                self._append_event(
                    connection,
                    "retry_authorization_consumed",
                    "operator_control",
                    retry_control["id"],
                    {"sha": request["sha"], "run_id": run_id, "attempt": attempt},
                )
            self._append_event(
                connection,
                "run_created",
                "deployment_run",
                run_id,
                {"request_id": request_id, "sha": request["sha"], "attempt": attempt},
            )
            return self._run_dict(connection.execute("SELECT * FROM deployment_runs WHERE id = ?", (run_id,)).fetchone())

        return self._transaction(operation)

    def claim_run_execution(
        self, run_id: str, trusted_prod_sha: str, executor_identity: str
    ) -> dict[str, Any]:
        """Atomically fence a planned run immediately before executable work."""
        run_id = self._validate_identifier("run_id", run_id, 64)
        trusted_prod_sha = self._validate_sha(trusted_prod_sha)
        executor_identity = self._validate_identifier("executor_identity", executor_identity, MAX_ACTOR_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            run = connection.execute("SELECT * FROM deployment_runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                raise ValidationError(f"unknown deployment run {run_id}")
            if run["status"] != "planned":
                raise InvalidTransition("only a planned deployment run may claim executable work")
            if run["sha"] != trusted_prod_sha:
                self._finish_run(
                    connection,
                    run,
                    "superseded",
                    "authoritative_prod_head_changed",
                    {"code": "AUTHORITATIVE_HEAD_CHANGED", "new_sha": trusted_prod_sha},
                )
                return self._run_dict(connection.execute("SELECT * FROM deployment_runs WHERE id = ?", (run_id,)).fetchone())
            desired = self._desired_state(connection, trusted_prod_sha)
            if desired["state"] != "ready" or desired["request"]["id"] != run["request_id"]:
                raise ExecutionBlocked(f"deployment run is fenced by current state: {desired['reason']}")
            now = utc_now()
            connection.execute(
                """
                UPDATE deployment_runs
                SET status = 'running', execution_claimed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, run_id),
            )
            self._append_event(
                connection,
                "run_execution_claimed",
                "deployment_run",
                run_id,
                {"executor_identity": executor_identity, "trusted_prod_sha": trusted_prod_sha},
            )
            return self._run_dict(connection.execute("SELECT * FROM deployment_runs WHERE id = ?", (run_id,)).fetchone())

        return self._transaction(operation)

    def transition_run(
        self,
        run_id: str,
        target_status: str,
        outcome: str | None = None,
        error: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        run_id = self._validate_identifier("run_id", run_id, 64)
        if target_status not in RUN_TRANSITIONS:
            raise ValidationError(f"unknown deployment-run status {target_status!r}")
        if outcome is not None:
            outcome = self._validate_text("outcome", outcome, MAX_REASON_LENGTH)
        error_details = self._validate_error_details(error)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            run = connection.execute("SELECT * FROM deployment_runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                raise ValidationError(f"unknown deployment run {run_id}")
            current_status = run["status"]
            if current_status in {"migration_boundary_claimed", "migration_uncertain"}:
                raise MigrationBoundaryActive(
                    "a post-boundary run can change only through migration-ledger reconciliation"
                )
            if target_status not in RUN_TRANSITIONS[current_status]:
                raise InvalidTransition(f"cannot transition deployment run from {current_status} to {target_status}")
            self._finish_run(connection, run, target_status, outcome, error)
            return self._run_dict(connection.execute("SELECT * FROM deployment_runs WHERE id = ?", (run_id,)).fetchone())

        return self._transaction(operation)

    def pause(self, operator: str, reason: str) -> dict[str, Any]:
        return self._activate_control("pause", None, operator, reason)

    def resume(self, operator: str, reason: str) -> dict[str, Any] | None:
        return self._release_control("pause", None, operator, reason)

    def sideline(self, sha: str, operator: str, reason: str) -> dict[str, Any]:
        return self._activate_control("sideline", self._validate_sha(sha), operator, reason)

    def release(self, sha: str, operator: str, reason: str) -> dict[str, Any] | None:
        return self._release_control("sideline", self._validate_sha(sha), operator, reason)

    def set_rollback_hold(self, sha: str, operator: str, reason: str) -> dict[str, Any]:
        return self._activate_control("rollback_hold", self._validate_sha(sha), operator, reason)

    def clear_rollback_hold(self, operator: str, reason: str) -> dict[str, Any] | None:
        return self._release_control("rollback_hold", None, operator, reason)

    def authorize_retry(self, sha: str, operator: str, reason: str) -> dict[str, Any]:
        """Create one durable, single-use retry permission for a failed SHA."""
        sha = self._validate_sha(sha)
        operator = self._validate_identifier("operator", operator, MAX_ACTOR_LENGTH)
        reason = self._validate_text("reason", reason, MAX_REASON_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            latest_run = connection.execute(
                "SELECT * FROM deployment_runs WHERE sha = ? ORDER BY attempt DESC LIMIT 1", (sha,)
            ).fetchone()
            if not latest_run or not self._run_requires_operator_retry(latest_run):
                raise InvalidTransition(
                    "a retry authorization requires the latest run for the exact SHA to have failed or been canceled after execution began"
                )
            existing = connection.execute(
                """
                SELECT * FROM operator_controls
                WHERE control_type = 'retry' AND target_sha = ? AND status = 'active'
                LIMIT 1
                """,
                (sha,),
            ).fetchone()
            if existing:
                return {"created": False, "control": self._control_dict(existing)}
            control_id = _new_id()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO operator_controls
                    (id, control_type, target_sha, status, reason, created_by, created_at)
                VALUES (?, 'retry', ?, 'active', ?, ?, ?)
                """,
                (control_id, sha, reason, operator, now),
            )
            self._append_event(
                connection,
                "retry_authorized",
                "operator_control",
                control_id,
                {"sha": sha, "operator": operator},
            )
            return {
                "created": True,
                "control": self._control_dict(
                    connection.execute("SELECT * FROM operator_controls WHERE id = ?", (control_id,)).fetchone()
                ),
            }

        return self._transaction(operation)

    def cancel_retry(self, sha: str, operator: str, reason: str) -> dict[str, Any]:
        """Withdraw one unused retry authorization without erasing its history."""
        sha = self._validate_sha(sha)
        operator = self._validate_identifier("operator", operator, MAX_ACTOR_LENGTH)
        reason = self._validate_text("reason", reason, MAX_REASON_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            active = connection.execute(
                """
                SELECT * FROM operator_controls
                WHERE control_type = 'retry' AND target_sha = ? AND status = 'active'
                LIMIT 1
                """,
                (sha,),
            ).fetchone()
            if not active:
                latest = connection.execute(
                    """
                    SELECT * FROM operator_controls
                    WHERE control_type = 'retry' AND target_sha = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (sha,),
                ).fetchone()
                if latest and latest["status"] == "consumed":
                    outcome = "already_consumed"
                elif latest and latest["status"] == "released":
                    outcome = "already_canceled"
                else:
                    outcome = "no_retry_authorization"
                return {"canceled": False, "outcome": outcome, "control": self._control_dict(latest)}
            now = utc_now()
            connection.execute(
                """
                UPDATE operator_controls
                SET status = 'released', released_by = ?, released_reason = ?, released_at = ?
                WHERE id = ?
                """,
                (operator, reason, now, active["id"]),
            )
            self._append_event(
                connection,
                "retry_authorization_canceled",
                "operator_control",
                active["id"],
                {"sha": sha, "operator": operator},
            )
            control = connection.execute("SELECT * FROM operator_controls WHERE id = ?", (active["id"],)).fetchone()
            return {"canceled": True, "outcome": "canceled", "control": self._control_dict(control)}

        return self._transaction(operation)

    def active_controls(self) -> list[dict[str, Any]]:
        return self._read(self._active_controls)

    def approve_migration(
        self, sha: str, plan_fingerprint: str, operator: str, reason: str, required_migration_count: int = 1
    ) -> dict[str, Any]:
        sha = self._validate_sha(sha)
        plan_fingerprint = self._validate_plan(plan_fingerprint)
        operator = self._validate_identifier("operator", operator, MAX_ACTOR_LENGTH)
        reason = self._validate_text("reason", reason, MAX_REASON_LENGTH)
        required_migration_count = self._validate_migration_count(required_migration_count)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            if not connection.execute("SELECT 1 FROM deployment_requests WHERE sha = ?", (sha,)).fetchone():
                raise ValidationError("migration approval requires an admitted deployment SHA")
            known_plan = connection.execute(
                """
                SELECT required_migration_count FROM migration_decisions
                WHERE sha = ? AND plan_fingerprint = ?
                LIMIT 1
                """,
                (sha, plan_fingerprint),
            ).fetchone()
            if known_plan and known_plan["required_migration_count"] != required_migration_count:
                raise ValidationError("the exact plan fingerprint has a conflicting required migration count")
            active = connection.execute(
                """
                SELECT * FROM migration_decisions
                WHERE sha = ?
                  AND state IN ('approved', 'executing', 'uncertain')
                LIMIT 1
                """,
                (sha,),
            ).fetchone()
            if active:
                if active["plan_fingerprint"] != plan_fingerprint:
                    raise ControlConflict(
                        f"migration decision {active['id']} is already active for SHA {sha}; reconcile or revoke it first"
                    )
                if active["state"] == "approved":
                    return {"created": False, "decision": self._decision_dict(active)}
                if active["state"] in {"executing", "uncertain"}:
                    raise MigrationBoundaryActive(
                        f"migration decision {active['id']} may already have crossed its irreversible boundary"
                    )
            durable_commit = connection.execute(
                """
                SELECT id FROM migration_decisions
                WHERE sha = ? AND plan_fingerprint = ? AND state IN ('committed', 'partially_committed')
                LIMIT 1
                """,
                (sha, plan_fingerprint),
            ).fetchone()
            if durable_commit:
                raise InvalidTransition("an exact plan with durable committed migrations cannot be approved as new")
            decision_id = _new_id()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO migration_decisions
                    (id, sha, plan_fingerprint, required_migration_count, state, approved_by, approval_reason, approved_at)
                VALUES (?, ?, ?, ?, 'approved', ?, ?, ?)
                """,
                (decision_id, sha, plan_fingerprint, required_migration_count, operator, reason, now),
            )
            self._append_event(
                connection,
                "migration_approved",
                "migration_decision",
                decision_id,
                {
                    "sha": sha,
                    "plan_fingerprint": plan_fingerprint,
                    "required_migration_count": required_migration_count,
                    "operator": operator,
                },
            )
            row = connection.execute("SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)).fetchone()
            return {"created": True, "decision": self._decision_dict(row)}

        return self._transaction(operation)

    def revoke_migration(self, decision_id: str, operator: str, reason: str) -> dict[str, Any] | None:
        decision_id = self._validate_identifier("decision_id", decision_id, 64)
        operator = self._validate_identifier("operator", operator, MAX_ACTOR_LENGTH)
        reason = self._validate_text("reason", reason, MAX_REASON_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            decision = connection.execute(
                "SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if not decision:
                raise ValidationError(f"unknown migration decision {decision_id}")
            if decision["state"] == "revoked":
                return None
            if decision["state"] in {"executing", "uncertain"}:
                raise MigrationBoundaryActive(
                    "migration execution may have crossed its irreversible boundary; reconcile the application ledger first"
                )
            if decision["state"] != "approved":
                raise InvalidTransition("only an approved migration decision may be revoked")
            now = utc_now()
            connection.execute(
                """
                UPDATE migration_decisions
                SET state = 'revoked', revoked_by = ?, revocation_reason = ?, revoked_at = ?
                WHERE id = ?
                """,
                (operator, reason, now, decision_id),
            )
            self._append_event(
                connection, "migration_revoked", "migration_decision", decision_id, {"operator": operator}
            )
            return self._decision_dict(
                connection.execute("SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)).fetchone()
            )

        return self._transaction(operation)

    def claim_migration_boundary(
        self, run_id: str, decision_id: str, plan_fingerprint: str, trusted_prod_sha: str, executor_identity: str
    ) -> dict[str, Any]:
        """Durably claim the point before an executor may call PostgreSQL.

        This transaction is deliberately separate from PostgreSQL. Once it
        commits, revocation is forbidden until an external ledger observation
        reconciles the decision. The caller must not execute SQL before this
        method returns successfully.
        """
        run_id = self._validate_identifier("run_id", run_id, 64)
        decision_id = self._validate_identifier("decision_id", decision_id, 64)
        plan_fingerprint = self._validate_plan(plan_fingerprint)
        trusted_prod_sha = self._validate_sha(trusted_prod_sha)
        executor_identity = self._validate_identifier("executor_identity", executor_identity, MAX_ACTOR_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            run = connection.execute("SELECT * FROM deployment_runs WHERE id = ?", (run_id,)).fetchone()
            if not run or run["status"] != "running":
                raise InvalidTransition("only a running deployment run may claim a migration boundary")
            if run["sha"] != trusted_prod_sha:
                raise ExecutionBlocked("trusted prod SHA no longer matches the running deployment run")
            desired = self._desired_state(connection, trusted_prod_sha)
            if desired["state"] != "ready" or desired["request"]["sha"] != run["sha"]:
                raise ExecutionBlocked(f"migration boundary is fenced by current state: {desired['reason']}")
            decision = connection.execute("SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)).fetchone()
            if not decision or decision["state"] != "approved":
                raise InvalidTransition("only an active approved migration decision may claim the boundary")
            if decision["sha"] != run["sha"] or decision["plan_fingerprint"] != plan_fingerprint:
                raise ValidationError("migration decision does not exactly match the run SHA and plan fingerprint")
            now = utc_now()
            connection.execute(
                """
                UPDATE migration_decisions
                SET state = 'executing', boundary_claimed_by = ?, boundary_claimed_at = ?
                WHERE id = ?
                """,
                (executor_identity, now, decision_id),
            )
            connection.execute(
                """
                UPDATE deployment_runs
                SET status = 'migration_boundary_claimed', migration_decision_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (decision_id, now, run_id),
            )
            self._append_event(
                connection,
                "migration_boundary_claimed",
                "migration_decision",
                decision_id,
                {"executor_identity": executor_identity, "run_id": run_id, "plan_fingerprint": plan_fingerprint},
            )
            return self._decision_dict(connection.execute("SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)).fetchone())

        return self._transaction(operation)

    def mark_migration_uncertain(self, decision_id: str, executor_identity: str, reason: str) -> dict[str, Any]:
        """Record an ambiguous post-boundary result without inventing success."""
        decision_id = self._validate_identifier("decision_id", decision_id, 64)
        executor_identity = self._validate_identifier("executor_identity", executor_identity, MAX_ACTOR_LENGTH)
        reason = self._validate_text("reason", reason, MAX_REASON_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            decision = connection.execute(
                "SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if not decision:
                raise ValidationError(f"unknown migration decision {decision_id}")
            if decision["state"] != "executing":
                raise InvalidTransition("only a boundary-claimed migration decision may become uncertain")
            now = utc_now()
            connection.execute(
                """
                UPDATE migration_decisions
                SET state = 'uncertain', uncertain_by = ?, uncertain_reason = ?, uncertain_at = ?
                WHERE id = ?
                """,
                (executor_identity, reason, now, decision_id),
            )
            connection.execute(
                "UPDATE deployment_runs SET status = 'migration_uncertain', updated_at = ? WHERE migration_decision_id = ?",
                (now, decision_id),
            )
            self._append_event(
                connection,
                "migration_outcome_uncertain",
                "migration_decision",
                decision_id,
                {"executor_identity": executor_identity, "reason": reason},
            )
            return self._decision_dict(
                connection.execute("SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)).fetchone()
            )

        return self._transaction(operation)

    def reconcile_migration_ledger(
        self,
        decision_id: str,
        observed_ledger_fingerprint: str,
        committed_plan_migrations: int,
        executor_identity: str,
    ) -> dict[str, Any]:
        """Persist a verified ledger observation without executing PostgreSQL.

        The caller must derive the ledger fingerprint from the real durable
        application ledger. A partial result remains historical truth and
        never authorizes retrying migrations that ledger evidence says applied.
        """
        return self._reconcile_migration_outcome(
            decision_id, observed_ledger_fingerprint, committed_plan_migrations, executor_identity
        )

    def record_migration_committed(
        self, decision_id: str, executor_identity: str, observed_ledger_fingerprint: str
    ) -> dict[str, Any]:
        """Compatibility helper for a verified ledger containing the whole plan."""
        decision = self._read(
            lambda connection: connection.execute(
                "SELECT required_migration_count FROM migration_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        )
        if not decision:
            raise ValidationError(f"unknown migration decision {decision_id}")
        return self._reconcile_migration_outcome(
            decision_id, observed_ledger_fingerprint, int(decision["required_migration_count"]), executor_identity
        )

    def record_migration_not_committed(
        self, decision_id: str, executor_identity: str, observed_ledger_fingerprint: str
    ) -> dict[str, Any]:
        """Compatibility helper for a verified ledger containing none of the plan."""
        return self._reconcile_migration_outcome(decision_id, observed_ledger_fingerprint, 0, executor_identity)

    def migration_decisions(self) -> list[dict[str, Any]]:
        return self._read_rows(
            "SELECT * FROM migration_decisions ORDER BY approved_at DESC", (), self._decision_dict
        )

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = self._validate_limit(limit)

        def convert(row: sqlite3.Row) -> dict[str, Any]:
            return {
                "sequence": row["sequence"],
                "at": row["at"],
                "kind": row["kind"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "details": json.loads(row["details"]),
            }

        return self._read_rows("SELECT * FROM deployment_events ORDER BY sequence DESC LIMIT ?", (limit,), convert)

    def status(self, verbose: bool = False, trusted_prod_sha: str | None = None) -> dict[str, Any]:
        if trusted_prod_sha is not None:
            trusted_prod_sha = self._validate_sha(trusted_prod_sha)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            active = self._active_controls(connection)
            result: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "desired": self._desired_state(connection, trusted_prod_sha),
                "active_controls": [
                    {"type": control["control_type"], "target_sha": control["target_sha"], "id": control["id"]}
                    for control in active
                ],
            }
            if verbose:
                result["requests"] = [
                    self._request_dict(row)
                    for row in connection.execute("SELECT * FROM deployment_requests ORDER BY sequence DESC").fetchall()
                ]
                result["runs"] = [
                    self._run_dict(row)
                    for row in connection.execute("SELECT * FROM deployment_runs ORDER BY created_at DESC").fetchall()
                ]
                result["migration_decisions"] = [
                    self._decision_dict(row)
                    for row in connection.execute("SELECT * FROM migration_decisions ORDER BY approved_at DESC").fetchall()
                ]
            return result

        return self._read(operation)

    def _desired_state(
        self, connection: sqlite3.Connection, trusted_prod_sha: str | None
    ) -> dict[str, Any]:
        controls = self._active_controls(connection)
        if any(control["control_type"] == "pause" for control in controls):
            return {"state": "blocked", "reason": "paused", "request": None}
        rollback_hold = next((control for control in controls if control["control_type"] == "rollback_hold"), None)
        if rollback_hold:
            return {
                "state": "blocked",
                "reason": "rollback_hold",
                "rollback_sha": rollback_hold["target_sha"],
                "request": None,
            }
        if trusted_prod_sha is None:
            has_candidate = connection.execute(
                "SELECT 1 FROM deployment_requests WHERE status = 'accepted' LIMIT 1"
            ).fetchone()
            if not has_candidate:
                return {"state": "idle", "request": None}
            return {"state": "blocked", "reason": "authoritative_prod_head_required", "request": None}
        row = connection.execute(
            """
            SELECT * FROM deployment_requests
            WHERE status = 'accepted' AND sha = ?
            ORDER BY sequence ASC
            LIMIT 1
            """,
            (trusted_prod_sha,),
        ).fetchone()
        if not row:
            return {
                "state": "blocked",
                "reason": "authoritative_prod_head_not_admitted",
                "authoritative_prod_sha": trusted_prod_sha,
                "request": None,
            }
        if any(control["control_type"] == "sideline" and control["target_sha"] == trusted_prod_sha for control in controls):
            return {
                "state": "blocked",
                "reason": "sidelined",
                "authoritative_prod_sha": trusted_prod_sha,
                "request": self._request_dict(row),
            }
        latest_run = connection.execute(
            "SELECT * FROM deployment_runs WHERE sha = ? ORDER BY attempt DESC LIMIT 1", (trusted_prod_sha,)
        ).fetchone()
        if latest_run and self._run_requires_operator_retry(latest_run):
            retry_authorized = connection.execute(
                """
                SELECT 1 FROM operator_controls
                WHERE control_type = 'retry' AND target_sha = ? AND status = 'active'
                LIMIT 1
                """,
                (trusted_prod_sha,),
            ).fetchone()
            if not retry_authorized:
                return {
                    "state": "blocked",
                    "reason": "retry_authorization_required",
                    "authoritative_prod_sha": trusted_prod_sha,
                    "latest_run_id": latest_run["id"],
                    "request": self._request_dict(row),
                }
        if latest_run and latest_run["status"] == "succeeded":
            return {
                "state": "blocked",
                "reason": "runtime_reconciliation_required",
                "authoritative_prod_sha": trusted_prod_sha,
                "latest_run_id": latest_run["id"],
                "request": self._request_dict(row),
            }
        return {
            "state": "ready",
            "authoritative_prod_sha": trusted_prod_sha,
            "request": self._request_dict(row),
        }

    def _finish_run(
        self,
        connection: sqlite3.Connection,
        run: sqlite3.Row,
        target_status: str,
        outcome: str | None,
        error: dict[str, str] | None,
    ) -> None:
        error_details = self._validate_error_details(error)
        now = utc_now()
        connection.execute(
            """
            UPDATE deployment_runs
            SET status = ?, outcome = ?, error_details = ?, updated_at = ?, terminal_at = ?
            WHERE id = ?
            """,
            (target_status, outcome, error_details, now, now if target_status in TERMINAL_RUN_STATES else None, run["id"]),
        )
        self._append_event(
            connection,
            "run_transitioned",
            "deployment_run",
            run["id"],
            {"from": run["status"], "to": target_status, "outcome": outcome, "error_details": error},
        )

    @staticmethod
    def _run_requires_operator_retry(run: sqlite3.Row) -> bool:
        """Return whether this terminal run might have performed executable work."""
        return run["status"] == "failed" or (
            run["status"] == "canceled" and run["execution_claimed_at"] is not None
        )

    def _reconcile_migration_outcome(
        self,
        decision_id: str,
        observed_ledger_fingerprint: str,
        committed_plan_migrations: int,
        executor_identity: str,
    ) -> dict[str, Any]:
        decision_id = self._validate_identifier("decision_id", decision_id, 64)
        observed_ledger_fingerprint = self._validate_plan(observed_ledger_fingerprint)
        committed_plan_migrations = self._validate_migration_count(committed_plan_migrations, allow_zero=True)
        executor_identity = self._validate_identifier("executor_identity", executor_identity, MAX_ACTOR_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            decision = connection.execute("SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)).fetchone()
            if not decision:
                raise ValidationError(f"unknown migration decision {decision_id}")
            if decision["state"] not in {"executing", "uncertain"}:
                raise InvalidTransition("only a boundary-claimed or uncertain migration can be reconciled from its ledger")
            required = int(decision["required_migration_count"])
            if committed_plan_migrations > required:
                raise ValidationError("observed committed plan migrations exceed the approved plan")
            now = utc_now()
            if committed_plan_migrations == required:
                connection.execute(
                    """
                    UPDATE migration_decisions
                    SET state = 'committed', observed_ledger_fingerprint = ?, observed_committed_migration_count = ?,
                        observed_by = ?, observed_at = ?, committed_by = ?, committed_at = ?
                    WHERE id = ?
                    """,
                    (
                        observed_ledger_fingerprint,
                        committed_plan_migrations,
                        executor_identity,
                        now,
                        executor_identity,
                        now,
                        decision_id,
                    ),
                )
                connection.execute(
                    "UPDATE deployment_runs SET status = 'migration_committed', updated_at = ? WHERE migration_decision_id = ?",
                    (now, decision_id),
                )
                kind = "migration_commit_recorded"
            else:
                state = "not_committed" if committed_plan_migrations == 0 else "partially_committed"
                connection.execute(
                    """
                    UPDATE migration_decisions
                    SET state = ?, observed_ledger_fingerprint = ?, observed_committed_migration_count = ?,
                        observed_by = ?, observed_at = ?, reconciled_by = ?, reconciled_at = ?
                    WHERE id = ?
                    """,
                    (
                        state,
                        observed_ledger_fingerprint,
                        committed_plan_migrations,
                        executor_identity,
                        now,
                        executor_identity,
                        now,
                        decision_id,
                    ),
                )
                run = connection.execute(
                    "SELECT * FROM deployment_runs WHERE migration_decision_id = ?", (decision_id,)
                ).fetchone()
                if run:
                    self._finish_run(
                        connection,
                        run,
                        "failed",
                        "migration_not_fully_committed",
                        {
                            "code": "MIGRATION_NOT_FULLY_COMMITTED",
                            "committed_plan_migrations": str(committed_plan_migrations),
                            "required_migration_count": str(required),
                        },
                    )
                kind = "migration_not_committed_recorded" if state == "not_committed" else "migration_partial_commit_recorded"
            self._append_event(
                connection,
                kind,
                "migration_decision",
                decision_id,
                {
                    "executor_identity": executor_identity,
                    "observed_ledger_fingerprint": observed_ledger_fingerprint,
                    "committed_plan_migrations": committed_plan_migrations,
                    "required_migration_count": required,
                },
            )
            return self._decision_dict(connection.execute("SELECT * FROM migration_decisions WHERE id = ?", (decision_id,)).fetchone())

        return self._transaction(operation)

    def _activate_control(
        self, control_type: str, target_sha: str | None, operator: str, reason: str
    ) -> dict[str, Any]:
        operator = self._validate_identifier("operator", operator, MAX_ACTOR_LENGTH)
        reason = self._validate_text("reason", reason, MAX_REASON_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            if control_type == "rollback_hold":
                conflicting_hold = connection.execute(
                    """
                    SELECT * FROM operator_controls
                    WHERE control_type = 'rollback_hold' AND status = 'active'
                    LIMIT 1
                    """
                ).fetchone()
                if conflicting_hold and conflicting_hold["target_sha"] != target_sha:
                    raise ControlConflict(
                        f"rollback hold already protects {conflicting_hold['target_sha']}; release it before selecting another target"
                    )
            existing = connection.execute(
                """
                SELECT * FROM operator_controls
                WHERE control_type = ? AND status = 'active'
                  AND (target_sha = ? OR (target_sha IS NULL AND ? IS NULL))
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (control_type, target_sha, target_sha),
            ).fetchone()
            if existing:
                return {"created": False, "control": self._control_dict(existing)}
            control_id = _new_id()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO operator_controls
                    (id, control_type, target_sha, status, reason, created_by, created_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?)
                """,
                (control_id, control_type, target_sha, reason, operator, now),
            )
            self._append_event(
                connection,
                "operator_control_activated",
                "operator_control",
                control_id,
                {"control_type": control_type, "target_sha": target_sha, "operator": operator},
            )
            return {
                "created": True,
                "control": self._control_dict(
                    connection.execute("SELECT * FROM operator_controls WHERE id = ?", (control_id,)).fetchone()
                ),
            }

        return self._transaction(operation)

    def _release_control(
        self, control_type: str, target_sha: str | None, operator: str, reason: str
    ) -> dict[str, Any] | None:
        operator = self._validate_identifier("operator", operator, MAX_ACTOR_LENGTH)
        reason = self._validate_text("reason", reason, MAX_REASON_LENGTH)

        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            if control_type == "rollback_hold" and target_sha is None:
                control = connection.execute(
                    """
                    SELECT * FROM operator_controls
                    WHERE control_type = 'rollback_hold' AND status = 'active'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            else:
                control = connection.execute(
                    """
                    SELECT * FROM operator_controls
                    WHERE control_type = ? AND status = 'active'
                      AND (target_sha = ? OR (target_sha IS NULL AND ? IS NULL))
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (control_type, target_sha, target_sha),
                ).fetchone()
            if not control:
                return None
            now = utc_now()
            connection.execute(
                """
                UPDATE operator_controls
                SET status = 'released', released_by = ?, released_reason = ?, released_at = ?
                WHERE id = ?
                """,
                (operator, reason, now, control["id"]),
            )
            self._append_event(
                connection,
                "operator_control_released",
                "operator_control",
                control["id"],
                {"control_type": control_type, "target_sha": target_sha, "operator": operator},
            )
            return self._control_dict(
                connection.execute("SELECT * FROM operator_controls WHERE id = ?", (control["id"],)).fetchone()
            )

        return self._transaction(operation)

    def _transaction(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        self.initialize()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.commit()
            return result
        except ControlPlaneError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise StateIntegrityError(f"SQLite invariant rejected the operation: {exc}") from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise StateIntegrityError(f"SQLite operation failed: {exc}") from exc
        finally:
            connection.close()

    def _read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        self.initialize()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            result = operation(connection)
            connection.commit()
            return result
        except ControlPlaneError:
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise StateIntegrityError(f"SQLite read failed: {exc}") from exc
        finally:
            connection.close()

    def _read_rows(
        self, query: str, parameters: tuple[Any, ...], converter: Callable[[sqlite3.Row], T]
    ) -> list[T]:
        return self._read(lambda connection: [converter(row) for row in connection.execute(query, parameters).fetchall()])

    def _connect(self) -> sqlite3.Connection:
        # Two processes opening a freshly created WAL database concurrently can
        # race while SQLite acquires the initial journal/schema lock. Retry ONLY
        # transient BUSY/LOCKED errors, never schema/corruption failures.
        for attempt in range(3):
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(
                    self.state_db,
                    timeout=BUSY_TIMEOUT_MS / 1_000,
                    isolation_level=None,
                    check_same_thread=False,
                )
                connection.row_factory = sqlite3.Row
                connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
                connection.execute("PRAGMA foreign_keys = ON")
                # WAL is durable once enabled. A read avoids repeatedly taking
                # a journal-mode write lock on every concurrent admission.
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0].lower()
                if journal_mode != "wal":
                    journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower()
                if journal_mode != "wal":
                    raise StateIntegrityError(f"SQLite WAL mode could not be enabled (got {journal_mode!r})")
                connection.execute("PRAGMA synchronous = FULL")
                return connection
            except (ControlPlaneError, OSError, sqlite3.DatabaseError) as exc:
                if connection is not None:
                    connection.close()
                if isinstance(exc, sqlite3.OperationalError) and attempt < 2 and (
                    "locked" in str(exc).lower() or "busy" in str(exc).lower()
                ):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                if isinstance(exc, ControlPlaneError):
                    raise
                raise StateIntegrityError(f"cannot open SQLite control-plane state: {exc}") from exc
        raise AssertionError("unreachable SQLite retry state")

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = [
            """
            CREATE TABLE control_plane_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version > 0),
                initialized_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE deployment_requests (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                event TEXT NOT NULL CHECK (event = 'push'),
                sha TEXT NOT NULL CHECK (length(sha) = 40),
                branch TEXT NOT NULL CHECK (branch = 'prod'),
                actor TEXT NOT NULL,
                source_id TEXT NOT NULL UNIQUE,
                canonical_payload TEXT NOT NULL,
                payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
                status TEXT NOT NULL CHECK (status = 'accepted'),
                accepted_at TEXT NOT NULL,
                UNIQUE (source_id, payload_hash)
            )
            """,
            """
            CREATE TABLE deployment_runs (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL REFERENCES deployment_requests(id) ON DELETE RESTRICT,
                sha TEXT NOT NULL CHECK (length(sha) = 40),
                attempt INTEGER NOT NULL CHECK (attempt > 0),
                status TEXT NOT NULL CHECK (status IN ('planned', 'running', 'migration_boundary_claimed', 'migration_uncertain', 'migration_committed', 'succeeded', 'failed', 'superseded', 'canceled')),
                actor TEXT NOT NULL,
                execution_claimed_at TEXT,
                migration_decision_id TEXT REFERENCES migration_decisions(id) ON DELETE RESTRICT,
                outcome TEXT,
                error_details TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                terminal_at TEXT
            )
            """,
            """
            CREATE TABLE operator_controls (
                id TEXT PRIMARY KEY,
                control_type TEXT NOT NULL CHECK (control_type IN ('pause', 'sideline', 'rollback_hold', 'retry')),
                target_sha TEXT CHECK (target_sha IS NULL OR length(target_sha) = 40),
                status TEXT NOT NULL CHECK (status IN ('active', 'released', 'consumed')),
                reason TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                released_by TEXT,
                released_reason TEXT,
                released_at TEXT,
                consumed_by TEXT,
                consumed_at TEXT,
                consumed_run_id TEXT REFERENCES deployment_runs(id) ON DELETE RESTRICT,
                CHECK ((control_type = 'pause' AND target_sha IS NULL) OR (control_type IN ('sideline', 'rollback_hold', 'retry') AND target_sha IS NOT NULL))
            )
            """,
            """
            CREATE TABLE migration_decisions (
                id TEXT PRIMARY KEY,
                sha TEXT NOT NULL CHECK (length(sha) = 40),
                plan_fingerprint TEXT NOT NULL CHECK (length(plan_fingerprint) = 64),
                required_migration_count INTEGER NOT NULL CHECK (required_migration_count > 0),
                state TEXT NOT NULL CHECK (state IN ('approved', 'revoked', 'executing', 'uncertain', 'committed', 'not_committed', 'partially_committed')),
                approved_by TEXT NOT NULL,
                approval_reason TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                revoked_by TEXT,
                revocation_reason TEXT,
                revoked_at TEXT,
                boundary_claimed_by TEXT,
                boundary_claimed_at TEXT,
                uncertain_by TEXT,
                uncertain_reason TEXT,
                uncertain_at TEXT,
                observed_ledger_fingerprint TEXT CHECK (observed_ledger_fingerprint IS NULL OR length(observed_ledger_fingerprint) = 64),
                observed_committed_migration_count INTEGER,
                observed_by TEXT,
                observed_at TEXT,
                committed_by TEXT,
                committed_at TEXT,
                reconciled_by TEXT,
                reconciled_at TEXT
            )
            """,
            """
            CREATE TABLE deployment_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                at TEXT NOT NULL,
                kind TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                details TEXT NOT NULL
            )
            """,
            "CREATE INDEX deployment_requests_sha_sequence ON deployment_requests (sha, sequence DESC)",
            "CREATE INDEX deployment_runs_request ON deployment_runs (request_id, created_at DESC)",
            "CREATE UNIQUE INDEX deployment_runs_sha_attempt ON deployment_runs (sha, attempt)",
            "CREATE UNIQUE INDEX one_active_deployment_run ON deployment_runs ((1)) WHERE status IN ('planned', 'running', 'migration_boundary_claimed', 'migration_uncertain', 'migration_committed')",
            "CREATE INDEX deployment_events_entity ON deployment_events (entity_type, entity_id, sequence DESC)",
            "CREATE UNIQUE INDEX one_active_pause ON operator_controls (control_type) WHERE control_type = 'pause' AND status = 'active'",
            "CREATE UNIQUE INDEX one_active_rollback_hold ON operator_controls (control_type) WHERE control_type = 'rollback_hold' AND status = 'active'",
            "CREATE UNIQUE INDEX one_active_sideline_per_sha ON operator_controls (control_type, target_sha) WHERE control_type = 'sideline' AND status = 'active'",
            "CREATE UNIQUE INDEX one_active_retry_per_sha ON operator_controls (control_type, target_sha) WHERE control_type = 'retry' AND status = 'active'",
            "CREATE UNIQUE INDEX one_active_migration_decision_per_sha ON migration_decisions (sha) WHERE state IN ('approved', 'executing', 'uncertain')",
            """
            CREATE TRIGGER deployment_events_append_only_update
            BEFORE UPDATE ON deployment_events
            BEGIN SELECT RAISE(ABORT, 'deployment_events are append-only'); END
            """,
            """
            CREATE TRIGGER deployment_events_append_only_delete
            BEFORE DELETE ON deployment_events
            BEGIN SELECT RAISE(ABORT, 'deployment_events are append-only'); END
            """,
        ]
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _require_schema_tables(connection: sqlite3.Connection) -> None:
        required_columns = {
            "control_plane_meta": {"singleton", "schema_version", "initialized_at"},
            "deployment_requests": {
                "sequence", "id", "event", "sha", "branch", "actor", "source_id", "canonical_payload",
                "payload_hash", "status", "accepted_at",
            },
            "deployment_runs": {
                "id", "request_id", "sha", "attempt", "status", "actor", "execution_claimed_at", "outcome", "error_details", "created_at",
                "updated_at", "terminal_at", "migration_decision_id",
            },
            "deployment_events": {"sequence", "id", "at", "kind", "entity_type", "entity_id", "details"},
            "operator_controls": {
                "id", "control_type", "target_sha", "status", "reason", "created_by", "created_at",
                "released_by", "released_reason", "released_at", "consumed_by", "consumed_at", "consumed_run_id",
            },
            "migration_decisions": {
                "id", "sha", "plan_fingerprint", "required_migration_count", "state", "approved_by", "approval_reason", "approved_at",
                "revoked_by", "revocation_reason", "revoked_at", "boundary_claimed_by", "boundary_claimed_at",
                "uncertain_by", "uncertain_reason", "uncertain_at", "observed_ledger_fingerprint",
                "observed_committed_migration_count", "observed_by", "observed_at", "committed_by", "committed_at",
                "reconciled_by", "reconciled_at",
            },
        }
        existing = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        missing = sorted(set(required_columns) - existing)
        if missing:
            raise StateIntegrityError(f"control-plane schema is incomplete; missing tables: {', '.join(missing)}")
        for table, expected in required_columns.items():
            actual = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            absent = sorted(expected - actual)
            if absent:
                raise StateIntegrityError(
                    f"control-plane schema is incomplete; {table} is missing columns: {', '.join(absent)}"
                )
        expected_indexes = {
            "deployment_requests_sha_sequence",
            "deployment_runs_request",
            "deployment_runs_sha_attempt",
            "deployment_events_entity",
            "one_active_deployment_run",
            "one_active_pause",
            "one_active_rollback_hold",
            "one_active_sideline_per_sha",
            "one_active_retry_per_sha",
            "one_active_migration_decision_per_sha",
        }
        actual_indexes = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }
        absent_indexes = sorted(expected_indexes - actual_indexes)
        if absent_indexes:
            raise StateIntegrityError(
                f"control-plane schema is incomplete; missing indexes: {', '.join(absent_indexes)}"
            )
        expected_triggers = {"deployment_events_append_only_update", "deployment_events_append_only_delete"}
        actual_triggers = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        }
        absent_triggers = sorted(expected_triggers - actual_triggers)
        if absent_triggers:
            raise StateIntegrityError(
                f"control-plane schema is incomplete; missing append-only audit triggers: {', '.join(absent_triggers)}"
            )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection, kind: str, entity_type: str, entity_id: str, details: dict[str, Any]
    ) -> None:
        connection.execute(
            """
            INSERT INTO deployment_events (id, at, kind, entity_type, entity_id, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_new_id(), utc_now(), kind, entity_type, entity_id, canonical_json(details)),
        )

    @staticmethod
    def _request_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "sequence": row["sequence"],
            "event": row["event"],
            "sha": row["sha"],
            "branch": row["branch"],
            "actor": row["actor"],
            "source_id": row["source_id"],
            "payload_hash": row["payload_hash"],
            "status": row["status"],
            "accepted_at": row["accepted_at"],
        }

    @staticmethod
    def _run_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = {key: row[key] for key in row.keys() if key != "error_details"}
        result["error_details"] = (
            json.loads(row["error_details"]) if row["error_details"] is not None else None
        )
        return result

    @staticmethod
    def _control_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _decision_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _validate_limit(limit: int) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValidationError("limit must be an integer from 1 through 1000")
        return limit

    @staticmethod
    def _validate_sha(value: Any) -> str:
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            raise ValidationError("sha must be exactly 40 lowercase hexadecimal characters")
        return value

    @staticmethod
    def _validate_plan(value: Any) -> str:
        if not isinstance(value, str) or not PLAN_RE.fullmatch(value):
            raise ValidationError("migration plan fingerprint must be exactly 64 lowercase hexadecimal characters")
        return value

    @staticmethod
    def _validate_migration_count(value: Any, allow_zero: bool = False) -> int:
        lower = 0 if allow_zero else 1
        if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= 10_000:
            qualifier = "zero through 10000" if allow_zero else "one through 10000"
            raise ValidationError(f"migration count must be an integer from {qualifier}")
        return value

    @staticmethod
    def _validate_identifier(name: str, value: Any, maximum: int) -> str:
        if not isinstance(value, str) or not value or len(value) > maximum or not IDENTIFIER_RE.fullmatch(value):
            raise ValidationError(f"{name} must be a bounded safe identifier")
        return value

    @staticmethod
    def _validate_text(name: str, value: Any, maximum: int) -> str:
        if not isinstance(value, str) or not value or len(value) > maximum or value != value.strip():
            raise ValidationError(f"{name} must be non-empty, bounded, and have no surrounding whitespace")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValidationError(f"{name} must not contain control characters")
        return value

    def _validate_error_details(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, dict) or not value or len(value) > 16:
            raise ValidationError("error details must be a non-empty object with at most 16 fields")
        validated: dict[str, str] = {}
        for key, detail in value.items():
            safe_key = self._validate_identifier("error detail key", key, 64)
            validated[safe_key] = self._validate_text("error detail value", detail, MAX_ERROR_LENGTH)
        return canonical_json(validated)

    def _validate_request(self, payload: Any) -> tuple[dict[str, str], str, str]:
        if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
            raise ValidationError("request must contain exactly event, sha, branch, actor, and source_id")
        if payload["event"] != "push":
            raise ValidationError("event must be 'push'")
        if payload["branch"] != "prod":
            raise ValidationError("branch must be 'prod'")
        validated = {
            "event": "push",
            "sha": self._validate_sha(payload["sha"]),
            "branch": "prod",
            "actor": self._validate_identifier("actor", payload["actor"], MAX_ACTOR_LENGTH),
            "source_id": self._validate_identifier("source_id", payload["source_id"], MAX_SOURCE_ID_LENGTH),
        }
        serialized = canonical_json(validated)
        return validated, serialized, hashlib.sha256(serialized.encode("ascii")).hexdigest()

    @staticmethod
    def _active_controls(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        return [
            {key: row[key] for key in row.keys()}
            for row in connection.execute(
                "SELECT * FROM operator_controls WHERE status = 'active' ORDER BY created_at, id"
            ).fetchall()
        ]
