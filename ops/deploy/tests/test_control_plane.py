from __future__ import annotations

import io
import json
import hashlib
import multiprocessing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ops.deploy.cli import main as cli_main
from ops.deploy.control_plane import (
    ControlConflict,
    ControlPlane,
    ExecutionBlocked,
    IdempotencyConflict,
    InvalidTransition,
    MigrationBoundaryActive,
    StateIntegrityError,
    UnsupportedSchemaVersion,
    ValidationError,
)


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
PLAN_A = "d" * 64
LEDGER_A = "f" * 64


def request_payload(sha: str = SHA_A, source_id: str = "event-1") -> dict[str, str]:
    return {
        "event": "push",
        "sha": sha,
        "branch": "prod",
        "actor": "trusted-operator",
        "source_id": source_id,
    }


def concurrent_submit(state_db: str, payload: dict[str, str], result_queue: multiprocessing.Queue) -> None:
    try:
        result_queue.put(("ok", ControlPlane(state_db).submit_request(payload)["created"]))
    except Exception as exc:  # pragma: no cover - asserted by parent process.
        result_queue.put(("error", type(exc).__name__, str(exc)))


def concurrent_pause(state_db: str, result_queue: multiprocessing.Queue) -> None:
    try:
        result_queue.put(("ok", ControlPlane(state_db).pause("trusted-operator", "maintenance")["control"]["id"]))
    except Exception as exc:  # pragma: no cover - asserted by parent process.
        result_queue.put(("error", type(exc).__name__, str(exc)))


def concurrent_create_run(state_db: str, request_id: str, sha: str, result_queue: multiprocessing.Queue) -> None:
    try:
        result_queue.put(("ok", ControlPlane(state_db).create_run(request_id, sha)["id"]))
    except Exception as exc:  # pragma: no cover - asserted by parent process.
        result_queue.put(("error", type(exc).__name__, str(exc)))


def concurrent_cancel_or_consume_retry(
    state_db: str, request_id: str, action: str, result_queue: multiprocessing.Queue
) -> None:
    try:
        control_plane = ControlPlane(state_db)
        if action == "cancel":
            result = control_plane.cancel_retry(SHA_A, "trusted-operator", "withdraw concurrent retry")
            result_queue.put(("cancel", result["canceled"], result["outcome"]))
        else:
            result = control_plane.create_run(request_id, SHA_A)
            result_queue.put(("consume", result["id"], result["attempt"]))
    except Exception as exc:  # pragma: no cover - asserted by parent process.
        result_queue.put((action, "error", type(exc).__name__, str(exc)))


def concurrent_boundary_or_revoke(
    state_db: str, run_id: str, decision_id: str, action: str, result_queue: multiprocessing.Queue
) -> None:
    try:
        control_plane = ControlPlane(state_db)
        if action == "claim":
            result = control_plane.claim_migration_boundary(
                run_id, decision_id, PLAN_A, SHA_A, "trusted-executor"
            )
        else:
            result = control_plane.revoke_migration(decision_id, "trusted-operator", "withdraw approval")
        result_queue.put(("ok", result["state"] if result else "already_revoked"))
    except Exception as exc:  # pragma: no cover - asserted by parent process.
        result_queue.put(("error", type(exc).__name__, str(exc)))


class InterleavingStatusControlPlane(ControlPlane):
    """Inject one committed writer after status begins its read snapshot."""

    def __init__(self, state_db: str) -> None:
        super().__init__(state_db)
        self.injected = False

    def _active_controls(self, connection: sqlite3.Connection) -> list[dict[str, object]]:
        controls = super()._active_controls(connection)
        if not self.injected:
            self.injected = True
            ControlPlane(self.state_db).pause("interleaving-writer", "writer committed after read began")
        return controls


class ControlPlaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.state_db = str(Path(self.temporary_directory.name) / "control-plane.sqlite3")
        self.control_plane = ControlPlane(self.state_db)

    def submit(self, sha: str = SHA_A, source_id: str = "event-1") -> dict[str, object]:
        return self.control_plane.submit_request(request_payload(sha, source_id))

    def test_empty_and_repeated_initialization_preserve_state(self) -> None:
        self.control_plane.initialize()
        self.assertEqual(self.control_plane.list_requests(), [])
        self.submit()
        self.control_plane.initialize()
        self.assertEqual(len(self.control_plane.list_requests()), 1)

    def test_rejects_unknown_newer_schema(self) -> None:
        self.control_plane.initialize()
        with sqlite3.connect(self.state_db) as connection:
            connection.execute("UPDATE control_plane_meta SET schema_version = 99")
        with self.assertRaises(UnsupportedSchemaVersion):
            ControlPlane(self.state_db).initialize()

    def test_refuses_pre_release_schema_without_resetting_data(self) -> None:
        self.submit()
        with sqlite3.connect(self.state_db) as connection:
            connection.execute("UPDATE control_plane_meta SET schema_version = 3")
        with self.assertRaises(UnsupportedSchemaVersion):
            ControlPlane(self.state_db).initialize()
        with sqlite3.connect(self.state_db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM deployment_requests").fetchone()[0], 1)

    def test_required_sqlite_pragmas(self) -> None:
        settings = self.control_plane.pragma_settings()
        self.assertEqual(settings["journal_mode"], "wal")
        self.assertEqual(settings["foreign_keys"], 1)
        self.assertEqual(settings["synchronous"], 2)
        self.assertGreaterEqual(settings["busy_timeout"], 5_000)

    def test_request_validation_and_canonicalization(self) -> None:
        accepted = self.submit()
        self.assertTrue(accepted["created"])
        expected_canonical = json.dumps(request_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.assertEqual(
            accepted["request"]["payload_hash"], hashlib.sha256(expected_canonical.encode("ascii")).hexdigest()
        )

        invalid_payloads = [
            {},
            {**request_payload(), "unexpected": "value"},
            {**request_payload(), "event": "pull_request"},
            {**request_payload(), "branch": "main"},
            {**request_payload(), "sha": "A" * 40},
            {**request_payload(), "actor": "unsafe operator"},
            {**request_payload(), "source_id": "source\ncontrol"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    self.control_plane.submit_request(payload)

    def test_duplicate_and_conflicting_idempotency(self) -> None:
        first = self.submit()
        repeat = self.submit()
        self.assertTrue(first["created"])
        self.assertFalse(repeat["created"])
        self.assertEqual(first["request"]["id"], repeat["request"]["id"])
        self.assertEqual(len(self.control_plane.history()), 1)
        with self.assertRaises(IdempotencyConflict):
            self.submit(SHA_B)
        self.assertEqual(len(self.control_plane.list_requests()), 1)
        self.assertEqual(len(self.control_plane.history()), 1)

    def test_request_and_audit_event_are_transactional(self) -> None:
        accepted = self.submit()
        events = self.control_plane.history()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "request_accepted")
        self.assertEqual(events[0]["entity_id"], accepted["request"]["id"])

    def test_concurrent_duplicate_submission_uses_one_request_and_event(self) -> None:
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        payload = request_payload(source_id="event-concurrent")
        processes = [context.Process(target=concurrent_submit, args=(self.state_db, payload, queue)) for _ in range(2)]
        for process in processes:
            process.start()
        results = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(result[0] for result in results), ["ok", "ok"])
        self.assertEqual(sum(bool(result[1]) for result in results), 1)
        self.assertEqual(len(self.control_plane.list_requests()), 1)
        self.assertEqual(len(self.control_plane.history()), 1)

    def test_run_transitions_and_terminal_invariants(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        with self.assertRaises(InvalidTransition):
            self.control_plane.transition_run(run["id"], "succeeded")
        running = self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        self.assertEqual(running["status"], "running")
        failed = self.control_plane.transition_run(
            run["id"],
            "failed",
            outcome="build_failed",
            error={"code": "BUILD_FAILED", "message": "safe error"},
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_details"], {"code": "BUILD_FAILED", "message": "safe error"})
        self.assertIsNotNone(failed["terminal_at"])
        next_request = self.submit(SHA_B, "event-invalid-error")["request"]
        next_run = self.control_plane.create_run(next_request["id"], SHA_B)
        with self.assertRaises(ValidationError):
            self.control_plane.transition_run(next_run["id"], "running", error="opaque error")
        with self.assertRaises(InvalidTransition):
            self.control_plane.transition_run(run["id"], "running")

    def test_only_current_eligible_request_can_receive_a_run(self) -> None:
        request_a = self.submit(SHA_A, "event-a")["request"]
        request_b = self.submit(SHA_B, "event-b")["request"]
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.create_run(request_a["id"], SHA_B)
        run_b = self.control_plane.create_run(request_b["id"], SHA_B)
        self.control_plane.pause("trusted-operator", "maintenance")
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.claim_run_execution(run_b["id"], SHA_B, "trusted-executor")
        self.control_plane.resume("trusted-operator", "maintenance complete")
        self.control_plane.set_rollback_hold(SHA_A, "trusted-operator", "manual rollback")
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.create_run(request_a["id"], SHA_A)

    def test_pause_resume_is_durable_and_idempotent(self) -> None:
        self.submit()
        first = self.control_plane.pause("trusted-operator", "maintenance")
        repeated = self.control_plane.pause("trusted-operator", "maintenance")
        self.assertTrue(first["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(self.control_plane.desired_state()["reason"], "paused")
        restarted = ControlPlane(self.state_db)
        self.assertEqual(restarted.desired_state()["reason"], "paused")
        released = restarted.resume("trusted-operator", "maintenance complete")
        self.assertEqual(released["status"], "released")
        self.assertIsNone(restarted.resume("trusted-operator", "already resumed"))
        self.assertEqual(restarted.desired_state(SHA_A)["state"], "ready")

    def test_concurrent_pause_has_one_active_control(self) -> None:
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [context.Process(target=concurrent_pause, args=(self.state_db, queue)) for _ in range(2)]
        for process in processes:
            process.start()
        results = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual({result[0] for result in results}, {"ok"}, results)
        self.assertEqual(len(set(result[1] for result in results)), 1)
        self.assertEqual(len(self.control_plane.active_controls()), 1)

    def test_sideline_release_and_obsolete_sha_behavior(self) -> None:
        self.submit(SHA_A, "event-a")
        self.submit(SHA_B, "event-b")
        sidelined = self.control_plane.sideline(SHA_B, "trusted-operator", "investigate B")
        self.assertTrue(sidelined["created"])
        self.assertEqual(self.control_plane.desired_state(SHA_B)["reason"], "sidelined")
        self.assertFalse(self.control_plane.sideline(SHA_B, "trusted-operator", "repeat")["created"])
        released = self.control_plane.release(SHA_B, "trusted-operator", "release B")
        self.assertEqual(released["status"], "released")
        self.assertEqual(self.control_plane.desired_state(SHA_B)["request"]["sha"], SHA_B)
        self.assertIsNone(self.control_plane.release(SHA_B, "trusted-operator", "already released"))

    def test_authoritative_head_prevents_arrival_order_and_sideline_fallback(self) -> None:
        request_a = self.submit(SHA_A, "delayed-a")["request"]
        self.submit(SHA_B, "current-b")
        self.assertEqual(self.control_plane.desired_state()["reason"], "authoritative_prod_head_required")
        self.assertEqual(self.control_plane.desired_state(SHA_A)["request"]["sha"], SHA_A)
        self.control_plane.sideline(SHA_B, "trusted-operator", "hold latest B")
        blocked = self.control_plane.desired_state(SHA_B)
        self.assertEqual(blocked["reason"], "sidelined")
        self.assertEqual(blocked["request"]["sha"], SHA_B)
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.create_run(request_a["id"], SHA_B)
        self.control_plane.release(SHA_B, "trusted-operator", "allow B after renewed verification")
        self.assertEqual(self.control_plane.desired_state(SHA_B)["state"], "ready")

    def test_single_active_run_is_database_enforced_across_processes(self) -> None:
        request = self.submit()["request"]
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(target=concurrent_create_run, args=(self.state_db, request["id"], SHA_A, queue))
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        results = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sum(result[0] == "ok" for result in results), 1)
        self.assertIn("ControlConflict", {result[1] for result in results if result[0] == "error"})
        self.assertEqual(len(self.control_plane.status(verbose=True, trusted_prod_sha=SHA_A)["runs"]), 1)

    def test_planned_run_rechecks_pause_and_sideline_before_execution(self) -> None:
        request = self.submit()["request"]
        paused_run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.pause("trusted-operator", "maintenance")
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.claim_run_execution(paused_run["id"], SHA_A, "trusted-executor")
        self.control_plane.resume("trusted-operator", "maintenance complete")
        self.assertEqual(
            self.control_plane.claim_run_execution(paused_run["id"], SHA_A, "trusted-executor")["status"], "running"
        )
        self.control_plane.transition_run(paused_run["id"], "failed", "safe_pre_migration_failure")

        second = self.submit(SHA_B, "sideline-after-plan")["request"]
        sidelined_run = self.control_plane.create_run(second["id"], SHA_B)
        self.control_plane.sideline(SHA_B, "trusted-operator", "hold B")
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.claim_run_execution(sidelined_run["id"], SHA_B, "trusted-executor")
        self.assertEqual(
            self.control_plane.status(verbose=True, trusted_prod_sha=SHA_B)["runs"][0]["status"], "planned"
        )

    def test_rollback_hold_conflict_is_domain_error(self) -> None:
        self.control_plane.set_rollback_hold(SHA_A, "trusted-operator", "protect A")
        with self.assertRaises(ControlConflict):
            self.control_plane.set_rollback_hold(SHA_B, "trusted-operator", "replace with B")
        active = self.control_plane.active_controls()
        self.assertEqual(active[0]["target_sha"], SHA_A)

    def test_failed_sha_does_not_block_newer_eligible_sha(self) -> None:
        first = self.submit(SHA_A, "event-a")["request"]
        run = self.control_plane.create_run(first["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(
            run["id"],
            "failed",
            outcome="migration_failed",
            error={"code": "MIGRATION_FAILED", "message": "safe failure"},
        )
        second = self.submit(SHA_B, "event-b")["request"]
        self.assertEqual(self.control_plane.create_run(second["id"], SHA_B)["sha"], SHA_B)

    def test_rollback_hold_blocks_until_released_without_replay(self) -> None:
        self.submit(SHA_A, "event-a")
        self.submit(SHA_B, "event-b")
        held = self.control_plane.set_rollback_hold(SHA_A, "trusted-operator", "manual rollback")
        self.assertTrue(held["created"])
        desired = self.control_plane.desired_state()
        self.assertEqual(desired["reason"], "rollback_hold")
        self.assertEqual(desired["rollback_sha"], SHA_A)
        self.control_plane.clear_rollback_hold("trusted-operator", "resume automatic reconciliation")
        self.assertEqual(self.control_plane.desired_state(SHA_B)["request"]["sha"], SHA_B)

    def test_migration_decision_scope_revocation_and_commit_boundary(self) -> None:
        self.submit(SHA_A, "event-a")
        approval = self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "reviewed plan")
        self.assertTrue(approval["created"])
        self.assertFalse(
            self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "same exact plan")["created"]
        )
        revoked = self.control_plane.revoke_migration(approval["decision"]["id"], "trusted-operator", "changed plan")
        self.assertEqual(revoked["state"], "revoked")
        self.assertIsNone(
            self.control_plane.revoke_migration(approval["decision"]["id"], "trusted-operator", "idempotent repeat")
        )
        renewed = self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "fresh approval")
        self.assertTrue(renewed["created"])
        self.assertNotEqual(approval["decision"]["id"], renewed["decision"]["id"])
        self.control_plane.revoke_migration(renewed["decision"]["id"], "trusted-operator", "use replacement plan")

        other_plan = "e" * 64
        committed = self.control_plane.approve_migration(SHA_A, other_plan, "trusted-operator", "reviewed replacement")
        request = self.control_plane.list_requests()[0]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "future-executor")
        self.control_plane.claim_migration_boundary(
            run["id"], committed["decision"]["id"], other_plan, SHA_A, "future-executor"
        )
        committed_decision = self.control_plane.record_migration_committed(
            committed["decision"]["id"], "future-executor", LEDGER_A
        )
        self.assertEqual(committed_decision["state"], "committed")
        with self.assertRaises(InvalidTransition):
            self.control_plane.revoke_migration(committed_decision["id"], "trusted-operator", "pretend undo")
        with self.assertRaises(ValidationError):
            self.control_plane.approve_migration(SHA_A, "not-a-fingerprint", "trusted-operator", "invalid")

    def test_one_active_migration_decision_per_sha_rejects_conflicting_plan(self) -> None:
        self.submit(SHA_A, "active-plan")
        self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "first plan")
        with self.assertRaises(ControlConflict):
            self.control_plane.approve_migration(SHA_A, "e" * 64, "trusted-operator", "conflicting plan")

    def test_migration_boundary_blocks_revocation_until_ledger_reconciliation(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        decision = self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "approved")["decision"]
        self.control_plane.claim_migration_boundary(run["id"], decision["id"], PLAN_A, SHA_A, "trusted-executor")
        with self.assertRaises(MigrationBoundaryActive):
            self.control_plane.revoke_migration(decision["id"], "trusted-operator", "too late")
        committed = self.control_plane.record_migration_committed(decision["id"], "trusted-executor", LEDGER_A)
        self.assertEqual(committed["state"], "committed")
        self.assertEqual(
            self.control_plane.status(verbose=True, trusted_prod_sha=SHA_A)["runs"][0]["status"], "migration_committed"
        )
        with self.assertRaises(InvalidTransition):
            self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "cannot reuse committed plan")

    def test_partial_migration_commit_is_durable_and_allows_fix_forward(self) -> None:
        request_a = self.submit(SHA_A, "partial-a")["request"]
        run_a = self.control_plane.create_run(request_a["id"], SHA_A)
        self.control_plane.claim_run_execution(run_a["id"], SHA_A, "trusted-executor")
        decision_a = self.control_plane.approve_migration(
            SHA_A, PLAN_A, "trusted-operator", "two migrations approved", required_migration_count=2
        )["decision"]
        self.control_plane.claim_migration_boundary(run_a["id"], decision_a["id"], PLAN_A, SHA_A, "trusted-executor")
        partial = self.control_plane.reconcile_migration_ledger(
            decision_a["id"], LEDGER_A, 1, "trusted-executor"
        )
        self.assertEqual(partial["state"], "partially_committed")
        self.assertEqual(partial["observed_committed_migration_count"], 1)
        self.assertEqual(partial["observed_ledger_fingerprint"], LEDGER_A)
        with self.assertRaises(InvalidTransition):
            self.control_plane.revoke_migration(partial["id"], "trusted-operator", "pretend partial migration was undone")
        recorded_partial = [
            decision for decision in self.control_plane.migration_decisions() if decision["id"] == partial["id"]
        ][0]
        self.assertEqual(recorded_partial["state"], "partially_committed")
        self.assertEqual(recorded_partial["observed_ledger_fingerprint"], LEDGER_A)
        self.assertEqual(recorded_partial["observed_committed_migration_count"], 1)
        run_after_partial = self.control_plane.status(verbose=True, trusted_prod_sha=SHA_A)["runs"][0]
        self.assertEqual(run_after_partial["status"], "failed")
        self.assertEqual(run_after_partial["outcome"], "migration_not_fully_committed")
        with self.assertRaises(InvalidTransition):
            self.control_plane.approve_migration(
                SHA_A, PLAN_A, "trusted-operator", "cannot replay partially committed plan", required_migration_count=2
            )

        request_b = self.submit(SHA_B, "fix-forward-after-partial")["request"]
        run_b = self.control_plane.create_run(request_b["id"], SHA_B)
        self.assertEqual(run_b["sha"], SHA_B)

    def test_same_sha_events_keep_canonical_request_and_retry_without_fake_ingress(self) -> None:
        first = self.submit(SHA_A, "same-sha-first")["request"]
        second = self.submit(SHA_A, "same-sha-second")["request"]
        self.assertEqual(self.control_plane.desired_state(SHA_A)["request"]["id"], first["id"])
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.create_run(second["id"], SHA_A)
        run = self.control_plane.create_run(first["id"], SHA_A)
        self.assertEqual(run["request_id"], first["id"])
        self.assertEqual(run["attempt"], 1)
        third = self.submit(SHA_A, "same-sha-third")["request"]
        self.assertEqual(self.control_plane.desired_state(SHA_A)["request"]["id"], first["id"])
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(run["id"], "failed", "safe failure")

        with self.assertRaises(ExecutionBlocked):
            self.control_plane.create_run(third["id"], SHA_A)
        retry = self.control_plane.authorize_retry(SHA_A, "trusted-operator", "reviewed retry")
        self.assertTrue(retry["created"])
        retried = self.control_plane.create_run(first["id"], SHA_A)
        self.assertEqual(retried["attempt"], 2)
        self.assertEqual(retried["request_id"], first["id"])
        controls = self.control_plane.status(verbose=True, trusted_prod_sha=SHA_A)["active_controls"]
        self.assertNotIn("retry", {control["type"] for control in controls})
        self.control_plane.claim_run_execution(retried["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(retried["id"], "failed", "second safe failure")
        self.control_plane.authorize_retry(SHA_A, "trusted-operator", "reviewed second retry")
        retried_again = self.control_plane.create_run(first["id"], SHA_A)
        self.assertEqual(retried_again["attempt"], 3)

    def test_retry_reuses_the_only_ingress_request(self) -> None:
        request = self.submit()["request"]
        first = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(first["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(first["id"], "failed", "safe failure")
        self.control_plane.authorize_retry(SHA_A, "trusted-operator", "reviewed retry")
        retry = self.control_plane.create_run(request["id"], SHA_A)
        self.assertEqual(retry["request_id"], request["id"])
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(len(self.control_plane.list_requests()), 1)

    def test_cancel_retry_withdraws_unused_authorization_and_allows_later_reauthorization(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(run["id"], "failed", "safe failure")
        authorized = self.control_plane.authorize_retry(SHA_A, "trusted-operator", "reviewed retry")["control"]

        canceled = self.control_plane.cancel_retry(SHA_A, "trusted-operator", "withdrawn before execution")
        self.assertTrue(canceled["canceled"])
        self.assertEqual(canceled["outcome"], "canceled")
        self.assertEqual(canceled["control"]["id"], authorized["id"])
        self.assertEqual(canceled["control"]["status"], "released")
        self.assertEqual(
            [event["kind"] for event in self.control_plane.history()].count("retry_authorization_canceled"), 1
        )
        self.assertEqual(self.control_plane.desired_state(SHA_A)["reason"], "retry_authorization_required")
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.create_run(request["id"], SHA_A)

        repeated = self.control_plane.cancel_retry(SHA_A, "trusted-operator", "repeat withdrawal")
        self.assertFalse(repeated["canceled"])
        self.assertEqual(repeated["outcome"], "already_canceled")
        replacement = self.control_plane.authorize_retry(SHA_A, "trusted-operator", "new reviewed retry")
        self.assertTrue(replacement["created"])
        self.assertNotEqual(replacement["control"]["id"], authorized["id"])

    def test_consumed_retry_authorization_cannot_be_canceled(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(run["id"], "failed", "safe failure")
        authorized = self.control_plane.authorize_retry(SHA_A, "trusted-operator", "reviewed retry")["control"]
        retried = self.control_plane.create_run(request["id"], SHA_A)

        canceled = self.control_plane.cancel_retry(SHA_A, "trusted-operator", "too late")
        self.assertFalse(canceled["canceled"])
        self.assertEqual(canceled["outcome"], "already_consumed")
        self.assertEqual(canceled["control"]["id"], authorized["id"])
        self.assertEqual(canceled["control"]["status"], "consumed")
        self.assertEqual(canceled["control"]["consumed_run_id"], retried["id"])

    def test_independent_process_cancel_and_retry_consumption_serialize_truthfully(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(run["id"], "failed", "safe failure")
        self.control_plane.authorize_retry(SHA_A, "trusted-operator", "reviewed retry")

        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=concurrent_cancel_or_consume_retry,
                args=(self.state_db, request["id"], action, queue),
            )
            for action in ("cancel", "consume")
        ]
        for process in processes:
            process.start()
        results = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0)

        retry_row = None
        with sqlite3.connect(self.state_db) as connection:
            retry_row = connection.execute(
                "SELECT status, consumed_run_id FROM operator_controls WHERE control_type = 'retry' AND target_sha = ?",
                (SHA_A,),
            ).fetchone()
        self.assertIsNotNone(retry_row)
        if retry_row[0] == "released":
            self.assertIn(("cancel", True, "canceled"), results)
            self.assertTrue(any(result[0] == "consume" and result[1] == "error" for result in results))
            self.assertEqual(len(self.control_plane.status(verbose=True, trusted_prod_sha=SHA_A)["runs"]), 1)
        else:
            self.assertEqual(retry_row[0], "consumed")
            self.assertTrue(any(result[0] == "consume" and result[1] != "error" for result in results))
            self.assertIn(("cancel", False, "already_consumed"), results)
            self.assertIsNotNone(retry_row[1])

    def test_no_plan_migrations_committed_is_distinct_from_partial(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        decision = self.control_plane.approve_migration(
            SHA_A, PLAN_A, "trusted-operator", "two migrations approved", required_migration_count=2
        )["decision"]
        self.control_plane.claim_migration_boundary(run["id"], decision["id"], PLAN_A, SHA_A, "trusted-executor")
        no_commit = self.control_plane.reconcile_migration_ledger(decision["id"], LEDGER_A, 0, "trusted-executor")
        self.assertEqual(no_commit["state"], "not_committed")
        with self.assertRaises(InvalidTransition):
            self.control_plane.revoke_migration(no_commit["id"], "trusted-operator", "reconciliation is immutable")
        renewed = self.control_plane.approve_migration(
            SHA_A, PLAN_A, "trusted-operator", "new approval after verified no commit", required_migration_count=2
        )
        self.assertTrue(renewed["created"])

    def test_uncertain_migration_requires_ledger_reconciliation_before_reapproval(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        decision = self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "approved")["decision"]
        self.control_plane.claim_migration_boundary(run["id"], decision["id"], PLAN_A, SHA_A, "trusted-executor")
        uncertain = self.control_plane.mark_migration_uncertain(decision["id"], "trusted-executor", "crash after PostgreSQL call")
        self.assertEqual(uncertain["state"], "uncertain")
        with self.assertRaises(MigrationBoundaryActive):
            self.control_plane.transition_run(run["id"], "failed", "pretend no migration happened")
        with self.assertRaises(MigrationBoundaryActive):
            self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "retry before ledger check")
        not_committed = self.control_plane.record_migration_not_committed(
            decision["id"], "trusted-executor", LEDGER_A
        )
        self.assertEqual(not_committed["state"], "not_committed")
        renewed = self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "ledger cleared retry")
        self.assertTrue(renewed["created"])

    def test_boundary_claim_and_revocation_are_serialized(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        decision = self.control_plane.approve_migration(SHA_A, PLAN_A, "trusted-operator", "approved")["decision"]
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=concurrent_boundary_or_revoke,
                args=(self.state_db, run["id"], decision["id"], action, queue),
            )
            for action in ("claim", "revoke")
        ]
        for process in processes:
            process.start()
        results = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0)
        final = [entry for entry in self.control_plane.migration_decisions() if entry["id"] == decision["id"]][0]
        self.assertIn(final["state"], {"executing", "revoked"})
        self.assertEqual(sum(result[0] == "ok" for result in results), 1)

    def test_failed_request_has_no_automatic_retry_but_newer_fix_forward_runs(self) -> None:
        request_a = self.submit(SHA_A, "attempt-a")["request"]
        run_a = self.control_plane.create_run(request_a["id"], SHA_A)
        self.control_plane.claim_run_execution(run_a["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(run_a["id"], "failed", "build_failed")
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.create_run(request_a["id"], SHA_A)
        request_b = self.submit(SHA_B, "fix-forward-b")["request"]
        self.assertEqual(self.control_plane.create_run(request_b["id"], SHA_B)["sha"], SHA_B)

    def test_reselected_superseded_sha_can_be_planned_again_without_retry(self) -> None:
        request_a = self.submit(SHA_A, "planned-a")["request"]
        run_a = self.control_plane.create_run(request_a["id"], SHA_A)
        request_b = self.submit(SHA_B, "newer-b")["request"]
        run_b = self.control_plane.create_run(request_b["id"], SHA_B)
        self.assertEqual(run_b["status"], "planned")
        runs = self.control_plane.status(verbose=True, trusted_prod_sha=SHA_B)["runs"]
        old = [run for run in runs if run["id"] == run_a["id"]][0]
        self.assertEqual(old["status"], "superseded")
        reselected_a = self.control_plane.create_run(request_a["id"], SHA_A)
        self.assertEqual(reselected_a["request_id"], request_a["id"])
        self.assertEqual(reselected_a["attempt"], 2)
        self.assertEqual(self.control_plane.claim_run_execution(reselected_a["id"], SHA_A, "trusted-executor")["status"], "running")

    def test_canceled_runs_distinguish_pre_execution_from_execution_capable_retry(self) -> None:
        request = self.submit()["request"]
        planned = self.control_plane.create_run(request["id"], SHA_A)
        canceled_planned = self.control_plane.transition_run(planned["id"], "canceled", "operator canceled before claim")
        self.assertIsNone(canceled_planned["execution_claimed_at"])
        replanned = self.control_plane.create_run(request["id"], SHA_A)
        self.assertEqual(replanned["attempt"], 2)
        self.control_plane.claim_run_execution(replanned["id"], SHA_A, "trusted-executor")
        canceled_running = self.control_plane.transition_run(replanned["id"], "canceled", "operator canceled after claim")
        self.assertIsNotNone(canceled_running["execution_claimed_at"])
        self.assertEqual(self.control_plane.desired_state(SHA_A)["reason"], "retry_authorization_required")
        self.control_plane.authorize_retry(SHA_A, "trusted-operator", "reviewed canceled execution")
        self.assertEqual(self.control_plane.create_run(request["id"], SHA_A)["attempt"], 3)

    def test_succeeded_sha_requires_future_runtime_reconciliation(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(run["id"], "succeeded", "runtime outcome must be observed externally")
        desired = self.control_plane.desired_state(SHA_A)
        self.assertEqual(desired["reason"], "runtime_reconciliation_required")
        with self.assertRaises(ExecutionBlocked):
            self.control_plane.create_run(request["id"], SHA_A)

    def test_cli_cancel_retry_emits_machine_readable_result(self) -> None:
        request = self.submit()["request"]
        run = self.control_plane.create_run(request["id"], SHA_A)
        self.control_plane.claim_run_execution(run["id"], SHA_A, "trusted-executor")
        self.control_plane.transition_run(run["id"], "failed", "safe failure")
        self.control_plane.authorize_retry(SHA_A, "trusted-operator", "reviewed retry")
        stdout = io.StringIO()
        exit_code = cli_main(
            [
                "--state-db",
                self.state_db,
                "cancel-retry",
                SHA_A,
                "--operator",
                "trusted-operator",
                "--reason",
                "withdrawn",
            ],
            stdout,
            io.StringIO(),
        )
        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["canceled"])
        self.assertEqual(result["control"]["status"], "released")

    def test_status_uses_one_snapshot_and_never_selects_without_authoritative_head(self) -> None:
        self.submit(SHA_A, "snapshot-a")
        self.submit(SHA_B, "snapshot-b")
        self.control_plane.pause("trusted-operator", "maintenance")
        status = self.control_plane.status(verbose=True, trusted_prod_sha=SHA_B)
        self.assertEqual(status["desired"]["reason"], "paused")
        self.assertEqual(status["active_controls"][0]["type"], "pause")
        self.assertEqual(len(status["requests"]), 2)
        self.control_plane.resume("trusted-operator", "done")
        self.assertEqual(self.control_plane.status()["desired"]["reason"], "authoritative_prod_head_required")

    def test_status_snapshot_excludes_a_writer_that_commits_mid_read(self) -> None:
        self.submit(SHA_A, "interleaving")
        snapshot_reader = InterleavingStatusControlPlane(self.state_db)
        snapshot = snapshot_reader.status(trusted_prod_sha=SHA_A)
        self.assertEqual(snapshot["desired"]["state"], "ready")
        self.assertEqual(snapshot["active_controls"], [])
        self.assertEqual(self.control_plane.status(trusted_prod_sha=SHA_A)["desired"]["reason"], "paused")

    def test_append_only_events_and_integrity_detection(self) -> None:
        self.submit()
        with sqlite3.connect(self.state_db) as connection:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DELETE FROM deployment_events")

        corrupt_db = Path(self.temporary_directory.name) / "corrupt.sqlite3"
        corrupt_db.write_bytes(b"not a sqlite database")
        with self.assertRaises(StateIntegrityError):
            ControlPlane(corrupt_db).initialize()

        with sqlite3.connect(self.state_db) as connection:
            connection.execute("DROP TRIGGER deployment_events_append_only_delete")
        with self.assertRaises(StateIntegrityError):
            ControlPlane(self.state_db).initialize()

    def test_cli_machine_output_and_error_codes(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        success = cli_main(
            [
                "--state-db",
                self.state_db,
                "submit",
                "--event",
                "push",
                "--sha",
                SHA_A,
                "--branch",
                "prod",
                "--actor",
                "trusted-operator",
                "--source-id",
                "cli-event",
            ],
            stdout,
            stderr,
        )
        self.assertEqual(success, 0)
        self.assertTrue(json.loads(stdout.getvalue())["created"])
        failure = cli_main(
            [
                "--state-db",
                self.state_db,
                "submit",
                "--event",
                "push",
                "--sha",
                "bad",
                "--branch",
                "prod",
                "--actor",
                "trusted-operator",
                "--source-id",
                "bad-event",
            ],
            io.StringIO(),
            stderr,
        )
        self.assertEqual(failure, 2)
        self.assertEqual(json.loads(stderr.getvalue().splitlines()[-1])["error"], "ValidationError")
