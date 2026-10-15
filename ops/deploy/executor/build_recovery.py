"""Pure, fail-closed assessment of an interrupted *build-only* deployment.

This code has no permission to access files, SQLite, a network, or execute a
command. It NEVER closes an active run or authorizes a retry. The caller must
establish trustworthy, fenced observations before passing them here; a passing
assessment is NOT an authorization to write deployment state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:@-]{0,63}$")


class RecoveryBlocked(ValueError):
    """Insufficient or conflicting evidence; leave durable state unchanged."""


@dataclass(frozen=True)
class BuildRecoveryAssessment:
    run_id: str
    candidate_sha: str
    previous_sha: str
    result: str = "preconditions_met_not_authorized"

    def as_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "candidate_sha": self.candidate_sha,
            "previous_sha": self.previous_sha,
            "result": self.result,
        }


# Only a trusted root-side observer under the executor lock may supply these
# facts. Do not expose an external API accepting them from a user or network.
REQUIRED_HOST_FACTS = frozenset({
    "exclusive_executor_lock_held",
    "executor_service_inactive",
    "executor_timer_disabled_and_inactive",
    "no_candidate_builder_processes",
    "candidate_prepared_artifact_absent",
    "candidate_release_artifact_absent",
    "candidate_temporary_artifacts_absent",
    "previous_runtime_identity_and_readiness_verified",
    "postgresql_ledger_matches_prebuild_baseline",
})


def assess_interrupted_build(
    *,
    run_id: str,
    candidate_sha: str,
    previous_sha: str,
    runs: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    active_controls: Sequence[Mapping[str, Any]],
    migration_decisions: Sequence[Mapping[str, Any]],
    host_facts: Mapping[str, Any],
) -> BuildRecoveryAssessment:
    """Recognize exactly one unresolved build intent, never a later stage.

    Preconditions only. A privileged recovery coordinator must acquire and
    retain the executor flock, establish quiescence, verify host/DB evidence,
    and perform a fresh atomic compare-and-transition with an audit event.
    Re-evaluation and approval are mandatory; this result is not a token.
    """
    if (not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id)
            or not isinstance(candidate_sha, str) or not SHA.fullmatch(candidate_sha)
            or not isinstance(previous_sha, str) or not SHA.fullmatch(previous_sha)
            or candidate_sha == previous_sha):
        raise RecoveryBlocked("invalid or conflicting recovery identity")

    if type(runs) not in (tuple, list) or len(runs) != 1:
        raise RecoveryBlocked("expected one and only one deployment run")
    run = runs[0]
    if not isinstance(run, Mapping) or (
        run.get("id") != run_id
        or run.get("sha") != candidate_sha
        or run.get("status") != "running"
        or type(run.get("attempt")) is not int
        or run["attempt"] != 1
        or run.get("outcome") is not None
        or not run.get("execution_claimed_at")
    ):
        raise RecoveryBlocked("run identity or interrupted status does not match")

    if type(active_controls) not in (tuple, list) or active_controls:
        raise RecoveryBlocked("active operator controls or invalid control evidence")
    if type(migration_decisions) not in (tuple, list) or migration_decisions:
        raise RecoveryBlocked("migration decision or invalid SQL evidence")

    if type(events) not in (tuple, list) or len(events) != 3:
        raise RecoveryBlocked("expected exactly three durable run events")
    kinds = ("run_created", "run_execution_claimed", "executor_checkpoint")
    for i, (event, kind) in enumerate(zip(events, kinds)):
        if not isinstance(event, Mapping) or event.get("kind") != kind:
            raise RecoveryBlocked(f"unexpected run event at index {i}")
        if event.get("entity_type") != "deployment_run" or event.get("entity_id") != run_id:
            raise RecoveryBlocked("event owner does not match the run")
        if type(event.get("sequence")) is not int or event["sequence"] < 1:
            raise RecoveryBlocked("missing or invalid durable event sequence")
        if i and event["sequence"] <= events[i - 1]["sequence"]:
            raise RecoveryBlocked("durable event sequence is non-monotonic")

    detail = events[2].get("details")
    if not isinstance(detail, Mapping) or (
        detail.get("stage") != "build"
        or detail.get("phase") != "intent"
        or detail.get("sha") != candidate_sha
        or not isinstance(detail.get("executor_identity"), str)
        or not detail["executor_identity"]
        or set(detail) != {"stage", "phase", "sha", "executor_identity"}
    ):
        raise RecoveryBlocked("outstanding checkpoint is not an exact build intent")

    if (not isinstance(host_facts, Mapping)
            or set(host_facts) != REQUIRED_HOST_FACTS
            or any(host_facts[key] is not True for key in REQUIRED_HOST_FACTS)):
        raise RecoveryBlocked("trusted host quiescence and identity evidence incomplete")

    return BuildRecoveryAssessment(run_id, candidate_sha, previous_sha)
