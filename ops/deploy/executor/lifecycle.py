"""Safety-focused deployment lifecycle around the durable schema-v4 control plane.

This module has no subprocess, root, PostgreSQL, or SSH authority. The caller
must supply a trusted, privilege-separated implementation of ExecutionPort.
Source tests exercise crash windows via an injected deterministic port; this
file itself cannot deploy anything. A real adapter and its installation must
pass separate host/privilege acceptance before activation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence, Any

from ops.deploy.control_plane import ControlPlane, ControlPlaneError, ExecutionBlocked, ValidationError
from ops.deploy.executor.authority import GitHubAuthority
from ops.deploy.executor.migration_plan import (
    MigrationIdentity, MigrationPlan, plan_migrations,
)
from ops.deploy.executor.preflight import evaluate_preflight


class UnsafeObservation(Exception):
    """An actual side effect cannot be established; never claim success."""


class ExecutionPort(Protocol):
    """Strict interface, NOT a shell command dispatcher.

    External provider MUST independently check exact SHA, no symlinks in
    sensitive paths, correct Unix identities, bounded operation timeouts,
    immutable pair identity, service configuration, runtime readiness, and
    PostgreSQL ledger provenance. Never accept arbitrary commands or paths.
    """
    def source_migrations(self, sha: str) -> Sequence[MigrationIdentity]: ...
    def ledger(self) -> tuple[bool, Sequence[Mapping[str, Any]]]: ...
    def build_release(self, sha: str) -> None: ...
    def release_present(self, sha: str) -> bool: ...
    def active_release(self) -> str: ...
    def activate_release(self, sha: str) -> None: ...
    def run_migrations(self, sha: str) -> None: ...
    def verify_runtime(self, sha: str) -> bool: ...
    def rollback_release(self, previous_sha: str) -> None: ...


@dataclass(frozen=True)
class ExecutionResult:
    state: str
    reason: str
    run_id: str | None
    sha: str | None

    def as_dict(self) -> dict[str, Any]:
        return dict(state=self.state, reason=self.reason, run_id=self.run_id, sha=self.sha)


def committed_prefix(plan: MigrationPlan, observed: Sequence[Mapping[str, Any]], present: bool,
                     starting: Sequence[Mapping[str, Any]]) -> tuple[str, int]:
    """Reconstruct known committed prefix, refusing gaps or mismatches.

    Go runs each migration in its own transaction. A partial failure can leave
    a strict prefix of the approved list committed. Never rely on exit codes.
    """
    if not present:
        raise UnsafeObservation("the PostgreSQL migration ledger disappeared")
    # Validate the entire actual ledger against source migration identities;
    # a half-committed application schema remains externally observable truth.
    from ops.deploy.executor.migration_plan import _validate_ledger
    from ops.deploy.control_plane import canonical_json
    import hashlib

    rows = _validate_ledger(observed)
    initial = _validate_ledger(starting)
    initial_index = {m.version: m for m in initial}
    permitted = set(initial_index) | {m.version for m in plan.pending}
    if (any(m.version not in permitted for m in rows) or
            any(m not in rows for m in initial)):
        raise UnsafeObservation("observed ledger differs from approved starting history")
    applied = {m.version: m for m in rows}
    committed = 0
    gap_seen = False
    for migration in plan.pending:
        row = applied.get(migration.version)
        if row is None:
            gap_seen = True
        elif row != migration or gap_seen:
            raise UnsafeObservation("migration ledger contains a non-prefix or conflicting change")
        else:
            committed += 1
    # Check ledger rows existing when the plan was approved have not changed.
    # Their precise prior identities are not stored in MigrationPlan, so the
    # caller must also compare ledger observation externally before the run.
    observed_fingerprint = hashlib.sha256(
        canonical_json([m.as_dict() for m in rows]).encode("ascii")
    ).hexdigest()
    return observed_fingerprint, committed


class DeploymentLifecycle:
    """One-shot non-reentrant driver; all active-run recovery is fail-closed."""

    def __init__(
        self, authority: GitHubAuthority, control: ControlPlane, port: ExecutionPort,
        executor_identity: str = "trusted-executor",
    ) -> None:
        self.authority = authority
        self.control = control
        self.port = port
        self.identity = executor_identity
        self._starting_ledger: Sequence[Mapping[str, Any]] = ()

    def _refresh(self, sha: str) -> None:
        self.authority.verify(sha)
        desired = self.control.desired_state(sha)
        if desired["state"] != "ready":
            raise ExecutionBlocked("operator control or candidate eligibility changed")

    def _observe_ledger(self, plan: MigrationPlan) -> tuple[str, int]:
        present, applied = self.port.ledger()
        return committed_prefix(plan, applied, present, self._starting_ledger)

    def inspect_interrupted(self) -> ExecutionResult | None:
        """Report ambiguous unfinished run. Never automatically replay effects."""
        snapshot = self.control.status(verbose=True)
        active = [r for r in snapshot["runs"] if r["status"] in {
            "planned", "running", "migration_boundary_claimed", "migration_uncertain", "migration_committed"
        }]
        if not active:
            return None
        run = active[0]
        checkpoints = self.control.run_checkpoints(run["id"])
        if run["status"] == "planned":
            return ExecutionResult("manual_reconciliation_required", "planned_run_not_replayed", run["id"], run["sha"])
        pending = [c for c in checkpoints if c["phase"] == "intent" and not any(
            d["stage"] == c["stage"] and d["phase"] == "observed" for d in checkpoints
        )]
        reason = "unreconciled_external_effect" if pending else "active_run_needs_runtime_reconciliation"
        if run["status"] in {"migration_boundary_claimed", "migration_uncertain"}:
            reason = "postgresql_ledger_must_be_reconciled"
        return ExecutionResult("manual_reconciliation_required", reason, run["id"], run["sha"])

    def execute_once(self) -> ExecutionResult:
        interrupted = self.inspect_interrupted()
        if interrupted is not None:
            return interrupted

        # Read-only preflight must succeed *before* creating executable work.
        try:
            verified = self.authority.verify()
            source = self.port.source_migrations(verified.sha)
            ledger_present, applied = self.port.ledger()
            preflight = evaluate_preflight(
                self.authority, self.control, source, applied, ledger_present=ledger_present,
            )
        except Exception:
            return ExecutionResult("blocked", "authority_or_preflight_unavailable", None, None)
        if preflight.authority.sha != verified.sha:
            return ExecutionResult("blocked", "prod_changed_during_preflight", None, verified.sha)
        if preflight.state != "ready":
            return ExecutionResult("blocked", preflight.reason, None, verified.sha)
        assert preflight.plan is not None and preflight.request_id is not None
        sha, plan = verified.sha, preflight.plan
        self._starting_ledger = tuple(dict(row) for row in applied)
        try:
            self._refresh(sha)
            # Already-current SHA never attempts to activate over itself.
            if not plan.pending and self.port.active_release() == sha:
                if self.port.verify_runtime(sha):
                    return ExecutionResult("already_current", "runtime_verified_without_deployment", None, sha)
                return ExecutionResult("blocked", "current_runtime_not_healthy", None, sha)
            run = self.control.create_run(preflight.request_id, sha, self.identity)
        except Exception:
            return ExecutionResult("blocked", "authority_or_run_fence_changed", None, sha)
        run_id = run["id"]
        try:
            self._refresh(sha)
            claim = self.control.claim_run_execution(run_id, sha, self.identity)
            if claim["status"] != "running":
                return ExecutionResult("blocked", "run_superseded", run_id, sha)
            self.control.checkpoint_run(run_id, "build", "intent", self.identity, sha=sha)
            self.port.build_release(sha)
            if not self.port.release_present(sha):
                raise UnsafeObservation("built release pair could not be positively verified")
            self.control.checkpoint_run(run_id, "build", "observed", self.identity, sha=sha)

            self._refresh(sha)
            present, now_rows = self.port.ledger()
            after_build = plan_migrations(sha, source, now_rows, ledger_present=present)
            if after_build.plan_fingerprint != plan.plan_fingerprint:
                raise ExecutionBlocked("database ledger changed after plan authorization")

            if plan.pending:
                assert preflight.migration_decision_id is not None
                # Atomic durable boundary: no external SQL before this returns.
                self.control.claim_migration_boundary(
                    run_id, preflight.migration_decision_id, plan.plan_fingerprint, sha, self.identity,
                )
                # A command failure is NOT authoritative. Only the ledger tells
                # us if all, a prefix, or no approved migrations committed.
                try:
                    self.port.run_migrations(sha)
                except Exception:
                    pass
                self._reconcile_after_sql(plan, preflight.migration_decision_id)
                current = self.control.status(verbose=True)["runs"]
                this_run = next(r for r in current if r["id"] == run_id)
                if this_run["status"] != "migration_committed":
                    return ExecutionResult("failed", "migration_not_fully_committed", run_id, sha)

            # NEVER deploy an obsolete SHA after a long build or SQL operation.
            self._refresh(sha)
            previous = self.port.active_release()
            if not isinstance(previous, str) or len(previous) != 40 or previous == sha:
                raise UnsafeObservation("previous immutable release identity unavailable or equals target")

            self._refresh(sha)
            self.control.checkpoint_run(run_id, "activation", "intent", self.identity, sha=sha, previous_sha=previous)
            self.port.activate_release(sha)
            if self.port.active_release() != sha:
                raise UnsafeObservation("target release activation not independently observable")
            self.control.checkpoint_run(run_id, "activation", "observed", self.identity, sha=sha, previous_sha=previous)

            self.control.checkpoint_run(run_id, "runtime_verification", "intent", self.identity, sha=sha)
            try:
                healthy = self.port.verify_runtime(sha)
            except Exception:
                healthy = False
            if healthy is not True:
                return self._rollback(run_id, sha, previous)
            self.control.checkpoint_run(run_id, "runtime_verification", "observed", self.identity, sha=sha)
            self.control.transition_run(run_id, "succeeded", "verified_runtime_identity_and_readiness")
            return ExecutionResult("succeeded", "verified_runtime", run_id, sha)
        except (KeyboardInterrupt, SystemExit):
            # Never rewrite a potentially in-progress side effect as failed.
            raise
        except Exception as exc:
            # Only mark failed if *no unresolved external effect exists*.
            # Operators must inspect ambiguous checkpoints on recovery.
            if not self._unresolved(run_id):
                try:
                    status = next(r["status"] for r in self.control.status(verbose=True)["runs"] if r["id"] == run_id)
                    if status == "planned":
                        self.control.transition_run(run_id, "canceled", "pre_execution_control_fence_changed")
                    elif status in {"running", "migration_committed"}:
                        self.control.transition_run(run_id, "failed", "execution_failed_before_ambiguous_effect", {
                            "code": "EXECUTION_BLOCKED", "stage": type(exc).__name__,
                        })
                except ControlPlaneError:
                    pass
            return ExecutionResult("manual_reconciliation_required" if self._unresolved(run_id) else "failed",
                                   "external_state_must_be_inspected" if self._unresolved(run_id) else "execution_failed",
                                   run_id, sha)

    def _unresolved(self, run_id: str) -> bool:
        checkpoints = self.control.run_checkpoints(run_id)
        ambiguous_checkpoint = any(
            c["phase"] == "intent" and not any(
                x["stage"] == c["stage"] and x["phase"] == "observed" for x in checkpoints
            ) for c in checkpoints
        )
        uncertain_sql = any(
            r["id"] == run_id and r["status"] in {"migration_boundary_claimed", "migration_uncertain"}
            for r in self.control.status(verbose=True)["runs"]
        )
        return ambiguous_checkpoint or uncertain_sql

    def _reconcile_after_sql(self, plan: MigrationPlan, decision_id: str) -> None:
        try:
            fp, prefix = self._observe_ledger(plan)
        except Exception:
            # SQLite boundary already committed. On unreadable/bogus ledger
            # leave the run blocked, never guess whether SQL was committed.
            self.control.mark_migration_uncertain(decision_id, self.identity, "ledger_observation_unavailable")
            raise UnsafeObservation("migration ledger cannot be safely observed") from None
        self.control.reconcile_migration_ledger(decision_id, fp, prefix, self.identity)

    def _rollback(self, run_id: str, sha: str, previous: str) -> ExecutionResult:
        # Persist a hold FIRST so the reconciler cannot immediately redeploy
        # the bad SHA or undo the operator's deliberate recovery.
        self.control.set_rollback_hold(previous, self.identity, "runtime_verification_failed")
        self.control.checkpoint_run(run_id, "rollback", "intent", self.identity, sha=sha, previous_sha=previous)
        try:
            self.port.rollback_release(previous)
            if self.port.active_release() != previous or not self.port.verify_runtime(previous):
                raise UnsafeObservation("rollback runtime not independently healthy")
            self.control.checkpoint_run(run_id, "rollback", "observed", self.identity, sha=sha, previous_sha=previous)
            self.control.transition_run(run_id, "failed", "activation_failed_rollback_verified", {
                "code": "RUNTIME_CHECK_FAILED",
            })
            return ExecutionResult("rolled_back_hold", "prior_runtime_verified", run_id, sha)
        except Exception:
            return ExecutionResult("manual_reconciliation_required", "rollback_outcome_uncertain", run_id, sha)
