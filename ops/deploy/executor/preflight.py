"""Read-only control-plane decision for a verified prod commit.

This is a *preflight*, not a deployer. It must not create deployment runs,
claim migration boundaries, issue subprocesses, read app secrets, or write to DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ops.deploy.control_plane import ControlPlane, ValidationError
from ops.deploy.executor.authority import GitHubAuthority, VerifiedAuthority
from ops.deploy.executor.migration_plan import (
    MigrationIdentity, MigrationPlan, plan_migrations, require_exact_approval,
)


@dataclass(frozen=True)
class Preflight:
    authority: VerifiedAuthority
    state: str
    reason: str
    request_id: str | None
    plan: MigrationPlan | None
    migration_decision_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "authority": {
                "sha": self.authority.sha,
                "ci_run_id": self.authority.ci_run_id,
                "ci_attempt": self.authority.ci_attempt,
                "branch": self.authority.branch,
            },
            "state": self.state,
            "reason": self.reason,
            "request_id": self.request_id,
            "migration_decision_id": self.migration_decision_id,
            "plan": self.plan.as_dict() if self.plan else None,
            "mutated": False,
        }


def evaluate_preflight(
    authority: GitHubAuthority,
    control: ControlPlane,
    source: Sequence[MigrationIdentity],
    ledger: Sequence[Mapping[str, Any]],
    *, ledger_present: bool,
) -> Preflight:
    verified = authority.verify()
    snapshot = control.status(verbose=True, trusted_prod_sha=verified.sha)
    desired = snapshot["desired"]
    if desired["state"] != "ready":
        return Preflight(verified, "blocked", desired.get("reason", "not_ready"), None, None, None)
    request_id = desired["request"]["id"]
    plan = plan_migrations(verified.sha, source, ledger, ledger_present=ledger_present)
    try:
        decision_id = require_exact_approval(plan, snapshot["migration_decisions"])
    except ValidationError:
        return Preflight(verified, "blocked", "migration_approval_required", request_id, plan, None)
    # Guard against another proc changing the controls during planning.
    # A future executor MUST repeat this check after build and at irreversible
    # boundaries. No 'ready' snapshot alone authorizes deploy or SQL.
    desired_again = control.desired_state(verified.sha)
    if desired_again.get("state") != "ready" or desired_again["request"]["id"] != request_id:
        return Preflight(verified, "blocked", "control_state_changed", request_id, plan, decision_id)
    return Preflight(verified, "ready", "exact_ci_and_ledger_verified", request_id, plan, decision_id)
