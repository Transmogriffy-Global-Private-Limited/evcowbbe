"""Adversarial tests for the *non-mutating* recovery eligibility contract."""
import copy
import unittest

from ops.deploy.executor.build_recovery import (
    RecoveryBlocked, REQUIRED_HOST_FACTS, assess_interrupted_build,
)

RUN_ID = "601f52ab46ed40e4add026a6b50e7d61"
SHA = "ddea3882d1b98d10506a959478e65a608136a01c"
OLD = "2ac184c818db49dddf598984b68349d1d33e4c47"


def fixture():
    return {
        "run_id": RUN_ID,
        "candidate_sha": SHA,
        "previous_sha": OLD,
        "runs": [{"id": RUN_ID, "sha": SHA, "status": "running", "attempt": 1,
                  "outcome": None, "execution_claimed_at": "2026-10-13T11:00:00Z"}],
        "events": [
            {"sequence": 4, "kind": "run_created", "entity_type": "deployment_run", "entity_id": RUN_ID},
            {"sequence": 5, "kind": "run_execution_claimed", "entity_type": "deployment_run", "entity_id": RUN_ID},
            {"sequence": 6, "kind": "executor_checkpoint", "entity_type": "deployment_run",
             "entity_id": RUN_ID, "details": {"stage": "build", "phase": "intent", "sha": SHA,
                                                  "executor_identity": "evcow-orchestrator"}},
        ],
        "active_controls": [],
        "migration_decisions": [],
        "host_facts": {key: True for key in REQUIRED_HOST_FACTS},
    }


class BuildRecoveryAssessmentTests(unittest.TestCase):
    def test_exact_build_intent_is_only_precondition_not_mutation_authority(self):
        result = assess_interrupted_build(**fixture())
        self.assertEqual(result.result, "preconditions_met_not_authorized")
        self.assertEqual((result.run_id, result.candidate_sha, result.previous_sha),
                         (RUN_ID, SHA, OLD))

    def assert_blocked(self, change):
        entry = fixture()
        change(entry)
        with self.assertRaises(RecoveryBlocked):
            assess_interrupted_build(**entry)

    def test_wrong_sha_and_run_id_and_same_sha_fail_closed(self):
        for key, value in (("run_id", "wrong"), ("candidate_sha", "x" * 40),
                           ("previous_sha", SHA)):
            with self.subTest(key=key):
                self.assert_blocked(lambda x, k=key, v=value: x.__setitem__(k, v))

    def test_active_or_second_or_terminal_runs_fail_closed(self):
        for field, value in (("status", "failed"), ("status", "migration_uncertain"),
                             ("attempt", 2), ("outcome", "failed"),
                             ("execution_claimed_at", None)):
            with self.subTest(field=field, value=value):
                self.assert_blocked(lambda x, f=field, v=value: x["runs"][0].__setitem__(f, v))
        self.assert_blocked(lambda x: x["runs"].append(copy.deepcopy(x["runs"][0])))

    def test_extra_missing_reordered_or_tampered_checkpoint_fails(self):
        self.assert_blocked(lambda x: x["events"].pop())
        self.assert_blocked(lambda x: x["events"].append(copy.deepcopy(x["events"][-1])))
        self.assert_blocked(lambda x: x["events"].reverse())
        self.assert_blocked(lambda x: x["events"][-1]["details"].__setitem__("phase", "observed"))
        for stage in ("activation", "runtime_verification", "rollback", "migration"):
            with self.subTest(stage=stage):
                self.assert_blocked(lambda x, s=stage: x["events"][-1]["details"].__setitem__("stage", s))
        self.assert_blocked(lambda x: x["events"][-1]["details"].__setitem__("unexpected", "value"))
        self.assert_blocked(lambda x: x["events"][1].__setitem__("entity_id", "wrong"))
        self.assert_blocked(lambda x: x["events"][1].__setitem__("sequence", 3))

    def test_control_or_any_migration_decision_blocks(self):
        self.assert_blocked(lambda x: x["active_controls"].append({"type": "pause"}))
        self.assert_blocked(lambda x: x["migration_decisions"].append({"state": "approved"}))

    def test_each_missing_or_false_host_fact_blocks(self):
        for field in sorted(REQUIRED_HOST_FACTS):
            with self.subTest(field=field):
                self.assert_blocked(lambda x, f=field: x["host_facts"].pop(f))
                self.assert_blocked(lambda x, f=field: x["host_facts"].__setitem__(f, False))
                self.assert_blocked(lambda x, f=field: x["host_facts"].__setitem__(f, 1))
        self.assert_blocked(lambda x: x["host_facts"].__setitem__("extra", True))


if __name__ == "__main__":
    unittest.main()
