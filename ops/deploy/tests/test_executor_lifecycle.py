"""Execution lifecycles in isolation. No shell, PostgreSQL or VPS access."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from ops.deploy.control_plane import ControlPlane, ControlConflict, InvalidTransition, ExecutionBlocked
from ops.deploy.executor.authority import GitHubAuthority, REPOSITORY
from ops.deploy.executor.lifecycle import DeploymentLifecycle, UnsafeObservation, committed_prefix
from ops.deploy.executor.migration_plan import MigrationIdentity, plan_migrations

A, B, OLD = "a" * 40, "b" * 40, "c" * 40


class FakePort:
    def __init__(self, source=(), applied=()):
        self.migrations = tuple(source)
        self.applied = [m.as_dict() for m in applied]
        self.current = OLD
        self.releases = {OLD}
        self.runtime_healthy = True
        self.calls = []
        self.migration_error = False
        self.migration_prefix = None
        self.fail_at = None
        self.on_build = lambda: None

    def _call(self, what):
        self.calls.append(what)
        if self.fail_at == what:
            raise RuntimeError("simulated process failure")

    def source_migrations(self, sha):
        self._call("source")
        return self.migrations

    def ledger(self):
        self._call("ledger")
        return True, list(self.applied)

    def build_release(self, sha):
        self._call("build")
        self.on_build()
        self.releases.add(sha)

    def release_present(self, sha):
        self._call("release_present")
        return sha in self.releases

    def active_release(self):
        self._call("active_release")
        return self.current

    def activate_release(self, sha):
        self._call("activate")
        self.current = sha

    def run_migrations(self, sha):
        self._call("migrate")
        pending = [m.as_dict() for m in self.migrations if m.version not in {
            x["version"] for x in self.applied
        }]
        to_apply = pending if self.migration_prefix is None else pending[:self.migration_prefix]
        self.applied.extend(to_apply)
        if self.migration_error:
            raise RuntimeError("SQL transaction failed after committed prefix")

    def verify_runtime(self, sha):
        self._call("verify_runtime")
        return self.runtime_healthy if sha == A else True

    def rollback_release(self, sha):
        self._call("rollback")
        self.current = sha


def authority_for(head):
    def get(path):
        if path.endswith("/branches/prod"):
            return {"commit": {"sha": head[0]}}
        if "actions/workflows/ci.yml/runs?" in path:
            return {"workflow_runs": [{"head_sha": head[0], "head_branch": "prod", "name": "Source CI",
                                      "event": "push", "status": "completed", "conclusion": "success",
                                      "head_repository": {"full_name": REPOSITORY}, "id": 100, "run_attempt": 1}]}
        raise AssertionError("unexpected API request")
    return GitHubAuthority(get)


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cp = ControlPlane(str(Path(tmp.name) / "control.sqlite3"))
        self.head = [A]
        self.port = FakePort()
        self.driver = DeploymentLifecycle(authority_for(self.head), self.cp, self.port)
        self.cp.submit_request({"event": "push", "branch": "prod", "actor": "source-ci",
                                "sha": A, "source_id": "ci:1"})

    def approve(self):
        plan = plan_migrations(A, self.port.migrations, self.port.applied, ledger_present=True)
        if plan.pending:
            return self.cp.approve_migration(A, plan.plan_fingerprint, "operator", "reviewed plan", len(plan.pending))

    def run_record(self):
        return self.cp.status(verbose=True)["runs"][0]

    def test_no_migration_success_and_durable_checkpoints(self):
        out = self.driver.execute_once()
        self.assertEqual(out.state, "succeeded")
        self.assertEqual(self.run_record()["status"], "succeeded")
        history = self.cp.run_checkpoints(out.run_id)
        self.assertEqual([(h["stage"], h["phase"]) for h in history], [
            ("build", "intent"), ("build", "observed"),
            ("activation", "intent"), ("activation", "observed"),
            ("runtime_verification", "intent"), ("runtime_verification", "observed"),
        ])
        self.assertEqual(self.port.current, A)
        self.assertIsNone(self.driver.inspect_interrupted())
        self.assertEqual(self.driver.execute_once().state, "blocked")

    def test_paused_and_missing_ingress_do_not_create_run(self):
        self.cp.pause("operator", "maintenance")
        self.assertEqual(self.driver.execute_once().state, "blocked")
        self.assertEqual(self.cp.status(verbose=True)["runs"], [])

    def test_head_moves_during_build_and_activation_is_blocked(self):
        self.port.on_build = lambda: self.head.__setitem__(0, B)
        out = self.driver.execute_once()
        self.assertEqual(out.state, "failed")
        self.assertNotIn("activate", self.port.calls)
        self.assertEqual(self.port.current, OLD)

    def test_pause_during_build_prevents_activation(self):
        self.port.on_build = lambda: self.cp.pause("operator", "stop now")
        self.assertEqual(self.driver.execute_once().state, "failed")
        self.assertNotIn("activate", self.port.calls)

    def test_unexpected_build_error_keeps_active_run_for_inspection(self):
        self.port.fail_at = "build"
        out = self.driver.execute_once()
        self.assertEqual(out.state, "manual_reconciliation_required")
        self.assertEqual(self.run_record()["status"], "running")
        self.assertEqual(self.driver.execute_once().reason, "unreconciled_external_effect")
        self.assertEqual(self.port.calls.count("build"), 1)

    def test_unknown_activation_outcome_never_retries_automatically(self):
        self.port.fail_at = "activate"
        self.assertEqual(self.driver.execute_once().state, "manual_reconciliation_required")
        self.assertEqual(self.driver.execute_once().state, "manual_reconciliation_required")
        self.assertEqual(self.port.calls.count("activate"), 1)

    def test_runtime_failure_rolls_back_under_hold(self):
        self.port.runtime_healthy = False
        out = self.driver.execute_once()
        self.assertEqual(out.state, "rolled_back_hold")
        self.assertEqual(self.port.current, OLD)
        self.assertEqual(self.run_record()["status"], "failed")
        self.assertTrue(any(c["control_type"] == "rollback_hold" for c in self.cp.active_controls()))
        self.assertEqual(self.driver.execute_once().reason, "rollback_hold")

    def test_runtime_probe_exception_uses_guarded_verified_rollback(self):
        self.port.fail_at = "verify_runtime"
        # The fault affects target AND previous; fallback cannot be verified.
        result = self.driver.execute_once()
        self.assertEqual(result.state, "manual_reconciliation_required")
        self.assertEqual(self.port.current, OLD)
        self.assertTrue(any(c["control_type"] == "rollback_hold" for c in self.cp.active_controls()))

    def test_rollback_failure_stays_ambiguous_and_held(self):
        self.port.runtime_healthy = False
        self.port.fail_at = "rollback"
        out = self.driver.execute_once()
        self.assertEqual(out.state, "manual_reconciliation_required")
        self.assertEqual(self.run_record()["status"], "running")
        self.assertTrue(any(c["control_type"] == "rollback_hold" for c in self.cp.active_controls()))

    def test_migration_approval_required_before_creating_run(self):
        self.port.migrations = (MigrationIdentity(1, "first", "1" * 64, 0),)
        out = self.driver.execute_once()
        self.assertEqual((out.state, out.reason), ("blocked", "migration_approval_required"))
        self.assertEqual(self.cp.status(verbose=True)["runs"], [])

    def test_full_migrations_reconciled_into_success(self):
        self.port.migrations = (MigrationIdentity(1, "first", "1" * 64, 0),)
        self.approve()
        out = self.driver.execute_once()
        self.assertEqual(out.state, "succeeded")
        self.assertEqual(self.cp.migration_decisions()[0]["state"], "committed")
        self.assertEqual(self.run_record()["status"], "succeeded")

    def test_partial_migration_is_recorded_and_does_not_activate(self):
        self.port.migrations = (MigrationIdentity(1, "first", "1" * 64, 0),
                                MigrationIdentity(2, "second", "2" * 64, 0))
        self.port.migration_prefix = 1
        self.port.migration_error = True
        self.approve()
        out = self.driver.execute_once()
        self.assertEqual(out.state, "failed")
        self.assertEqual(self.cp.migration_decisions()[0]["state"], "partially_committed")
        self.assertEqual(self.run_record()["status"], "failed")
        self.assertNotIn("activate", self.port.calls)

    def test_completed_ledger_is_truth_even_on_command_error(self):
        self.port.migrations = (MigrationIdentity(1, "first", "1" * 64, 0),)
        self.port.migration_error = True
        self.approve()
        self.assertEqual(self.driver.execute_once().state, "succeeded")
        self.assertEqual(self.cp.migration_decisions()[0]["state"], "committed")

    def test_ledger_unavailable_after_sql_remains_uncertain(self):
        self.port.migrations = (MigrationIdentity(1, "first", "1" * 64, 0),)
        self.approve()
        normal = self.port.ledger
        calls = [0]
        def intermittent():
            calls[0] += 1
            if calls[0] >= 3:
                raise OSError("database unavailable")
            return normal()
        self.port.ledger = intermittent
        self.assertEqual(self.driver.execute_once().state, "manual_reconciliation_required")
        self.assertEqual(self.cp.migration_decisions()[0]["state"], "uncertain")
        self.assertEqual(self.run_record()["status"], "migration_uncertain")

    def test_interrupt_after_build_intent_is_durable_across_process_restart(self):
        self.port.fail_at = "build"
        result = self.driver.execute_once()
        self.assertEqual(result.state, "manual_reconciliation_required")
        reopened = ControlPlane(self.cp.state_db)
        checkpoints = reopened.run_checkpoints(result.run_id)
        self.assertEqual([(x["stage"], x["phase"]) for x in checkpoints], [("build", "intent")])
        restarted = DeploymentLifecycle(authority_for(self.head), reopened, self.port)
        self.assertEqual(restarted.execute_once().reason, "unreconciled_external_effect")
        self.assertEqual(self.port.calls.count("build"), 1)

    def test_post_build_ledger_change_blocks_before_migrations_or_activation(self):
        m1 = MigrationIdentity(1, "first", "1" * 64, 0)
        self.port.migrations = (m1,)
        self.approve()
        self.port.on_build = lambda: self.port.applied.append(m1.as_dict())
        result = self.driver.execute_once()
        self.assertEqual(result.state, "failed")
        self.assertNotIn("migrate", self.port.calls)
        self.assertNotIn("activate", self.port.calls)

    def test_newer_b_fixes_forward_after_partial_a(self):
        m1 = MigrationIdentity(1, "first", "1" * 64, 0)
        m2 = MigrationIdentity(2, "second", "2" * 64, 0)
        self.port.migrations = (m1, m2)
        self.port.migration_prefix = 1
        self.port.migration_error = True
        self.approve()
        self.assertEqual(self.driver.execute_once().state, "failed")
        self.assertEqual([r["version"] for r in self.port.applied], [1])
        self.cp.submit_request({"event": "push", "branch": "prod", "actor": "source-ci",
                                "sha": B, "source_id": "ci:2"})
        self.head[0] = B
        self.port.migration_error = False
        self.port.migration_prefix = None
        b_plan = plan_migrations(B, [m1, m2], [m1.as_dict()], ledger_present=True)
        self.cp.approve_migration(B, b_plan.plan_fingerprint, "operator", "fix forward", 1)
        result = self.driver.execute_once()
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(self.port.current, B)
        self.assertEqual([r["version"] for r in self.port.applied], [1, 2])
        runs = self.cp.status(verbose=True)["runs"]
        self.assertEqual({r["sha"]: r["status"] for r in runs}, {A: "failed", B: "succeeded"})

    def test_already_running_sha_is_observed_not_redeployed(self):
        self.port.current = A
        self.port.releases.add(A)
        out = self.driver.execute_once()
        self.assertEqual(out.state, "already_current")
        self.assertEqual(self.cp.status(verbose=True)["runs"], [])
        self.assertNotIn("build", self.port.calls)

    def test_checkpoint_denies_rollback_without_hold_and_future_forward_work(self):
        r = self.cp.create_run(self.cp.list_requests()[0]["id"], A)
        self.cp.claim_run_execution(r["id"], A, "executor")
        self.cp.checkpoint_run(r["id"], "build", "intent", "executor", sha=A)
        self.cp.checkpoint_run(r["id"], "build", "observed", "executor", sha=A)
        self.cp.checkpoint_run(r["id"], "activation", "intent", "executor", sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(r["id"], "activation", "observed", "executor", sha=A, previous_sha=OLD)
        with self.assertRaises(ExecutionBlocked):
            self.cp.checkpoint_run(r["id"], "rollback", "intent", "executor", sha=A, previous_sha=OLD)
        self.cp.set_rollback_hold(OLD, "operator", "recover")
        self.cp.checkpoint_run(r["id"], "rollback", "intent", "executor", sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(r["id"], "rollback", "observed", "executor", sha=A, previous_sha=OLD)
        with self.assertRaises(ExecutionBlocked):
            self.cp.checkpoint_run(r["id"], "runtime_verification", "intent", "executor", sha=A)

    def test_checkpoint_rejects_fake_observation_and_conflict(self):
        r = self.cp.create_run(self.cp.list_requests()[0]["id"], A)
        self.cp.claim_run_execution(r["id"], A, "executor")
        with self.assertRaises(InvalidTransition):
            self.cp.checkpoint_run(r["id"], "build", "observed", "executor", sha=A)
        self.cp.checkpoint_run(r["id"], "build", "intent", "executor", sha=A)
        self.assertFalse(self.cp.checkpoint_run(r["id"], "build", "intent", "executor", sha=A)["created"])
        with self.assertRaises(ControlConflict):
            self.cp.checkpoint_run(r["id"], "build", "intent", "other", sha=A)
        with self.assertRaises(ExecutionBlocked):
            self.cp.checkpoint_run(r["id"], "activation", "intent", "executor", sha=A, previous_sha=OLD)
        self.assertEqual(len(self.cp.run_checkpoints(r["id"])), 1)

    def test_committed_prefix_rejects_gaps_extras_and_modified_start(self):
        m1 = MigrationIdentity(1, "first", "1" * 64, 0)
        m2 = MigrationIdentity(2, "second", "2" * 64, 0)
        m3 = MigrationIdentity(3, "third", "3" * 64, 0)
        plan = plan_migrations(A, [m1, m2, m3], [m1.as_dict()], ledger_present=True)
        with self.assertRaises(UnsafeObservation):
            committed_prefix(plan, [m1.as_dict(), m3.as_dict()], True, [m1.as_dict()])
        with self.assertRaises(UnsafeObservation):
            committed_prefix(plan, [m1.as_dict(), m2.as_dict(), {**m3.as_dict(), "checksum": "f" * 64}], True, [m1.as_dict()])
        with self.assertRaises(UnsafeObservation):
            committed_prefix(plan, [m2.as_dict()], True, [m1.as_dict()])
        fingerprint, count = committed_prefix(plan, [m1.as_dict(), m2.as_dict()], True, [m1.as_dict()])
        self.assertEqual(count, 1)
        self.assertEqual(len(fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
