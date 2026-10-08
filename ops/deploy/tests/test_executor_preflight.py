"""Deterministic adversarial tests for Burner 3 verification/planning source."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ops.deploy.control_plane import ControlPlane, ValidationError
from ops.deploy.executor.authority import (
    AuthorityUnavailable, GitHubAuthority, REPOSITORY, github_getter,
)
from ops.deploy.executor.migration_plan import (
    MigrationIdentity, plan_migrations, read_source_migrations, require_exact_approval,
)
from ops.deploy.executor.preflight import evaluate_preflight

SHA = "a" * 40
B = "b" * 40


def positive_run(*, sha=SHA, id=25, attempt=2, **changes):
    record = dict(head_sha=sha, head_branch="prod", name="Source CI", event="push",
                  status="completed", conclusion="success", id=id, run_attempt=attempt,
                  head_repository={"full_name": REPOSITORY})
    record.update(changes)
    return record


class GitHubAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.runs = [positive_run()]
        self.head = SHA

        def getter(path):
            self.calls.append(path)
            if path.endswith("/branches/prod"):
                return {"commit": {"sha": self.head}}
            if "actions/workflows/ci.yml/runs?" in path:
                return {"workflow_runs": self.runs}
            raise AssertionError("unexpected GitHub API path")

        self.verify = GitHubAuthority(getter)

    def test_accepts_authoritative_sha_with_exact_successful_ci(self):
        verified = self.verify.verify()
        self.assertEqual((verified.sha, verified.ci_run_id, verified.ci_attempt), (SHA, 25, 2))
        self.assertIn("head_sha=" + SHA, self.calls[-1])

    def test_rejects_prod_head_change_before_work(self):
        self.head = B
        with self.assertRaises(AuthorityUnavailable):
            self.verify.verify(SHA)
        self.assertEqual(len(self.calls), 1)

    def test_missing_or_invalid_branch_sha_rejected(self):
        for head in (None, "short", "A" * 40, 1):
            with self.subTest(head=head):
                self.head = head
                with self.assertRaises(AuthorityUnavailable):
                    self.verify.verify()

    def test_every_spoofed_or_non_successful_run_is_rejected(self):
        modified = [
            dict(head_sha=B), dict(head_branch="main"), dict(name="Other CI"),
            dict(event="workflow_dispatch"), dict(status="queued"), dict(conclusion="failure"),
            dict(head_repository={"full_name": "attacker/evcowbbe"}),
            dict(id=True), dict(run_attempt=0),
        ]
        for changes in modified:
            with self.subTest(changes=changes):
                self.runs = [positive_run(**changes)]
                with self.assertRaises(AuthorityUnavailable):
                    self.verify.verify()

    def test_workflow_candidate_must_exist(self):
        self.runs = []
        with self.assertRaises(AuthorityUnavailable):
            self.verify.verify()

    def test_successful_run_selects_latest_deterministically(self):
        self.runs = [positive_run(id=12, attempt=7), positive_run(id=13, attempt=1)]
        self.assertEqual(self.verify.verify().ci_run_id, 13)

    def test_getter_failure_is_fail_closed_and_redacted(self):
        getter = GitHubAuthority(lambda p: (_ for _ in ()).throw(ValueError("secret-example")))
        with self.assertRaises(AuthorityUnavailable) as ctx:
            getter.verify()
        self.assertNotIn("secret-example", str(ctx.exception))

    def test_transport_rejects_credentials_and_unapproved_urls(self):
        for key in ("", "secret\nmalicious"):
            with self.assertRaises(ValidationError):
                github_getter(key)
        getter = github_getter("test-credential")
        with self.assertRaises(ValidationError):
            getter("https://attacker.invalid")


class MigrationPlanTest(unittest.TestCase):
    def setUp(self):
        self.m1 = MigrationIdentity(1, "create_users", "1" * 64, 0)
        self.m2 = MigrationIdentity(2, "add_column", "2" * 64, 1)

    def test_empty_source_and_empty_ledger_no_migrations(self):
        plan = plan_migrations(SHA, [], [], ledger_present=True)
        self.assertFalse(plan.pending)
        self.assertFalse(plan.as_dict()["requires_approval"])
        self.assertIsNone(require_exact_approval(plan, []))

    def test_missing_ledger_rejected_even_with_no_migrations(self):
        with self.assertRaises(ValidationError):
            plan_migrations(SHA, [], [], ledger_present=False)

    def test_partial_migration_plan_only_includes_unapplied(self):
        plan = plan_migrations(SHA, [self.m1, self.m2], [self.m1.as_dict()], ledger_present=True)
        self.assertEqual(plan.pending, (self.m2,))
        self.assertEqual(plan.binary_schema_version, 2)
        self.assertEqual(len(plan.plan_fingerprint), 64)

    def test_repeated_plan_is_stable_independent_of_row_order(self):
        one = plan_migrations(SHA, [self.m1, self.m2], [self.m1.as_dict(), self.m2.as_dict()], ledger_present=True)
        two = plan_migrations(SHA, [self.m1, self.m2], [self.m2.as_dict(), self.m1.as_dict()], ledger_present=True)
        self.assertEqual(one, two)
        changed = plan_migrations(B, [self.m1, self.m2], [self.m1.as_dict()], ledger_present=True)
        self.assertNotEqual(one.plan_fingerprint, changed.plan_fingerprint)

    def test_applied_checksum_name_and_floor_mismatches_are_blocked(self):
        for key, bad in (("checksum", "f" * 64), ("name", "bad_name"), ("min_compatible_binary_version", 1)):
            with self.subTest(key=key):
                row = self.m1.as_dict()
                row[key] = bad
                with self.assertRaises(ValidationError):
                    plan_migrations(SHA, [self.m1], [row], ledger_present=True)

    def test_missing_lower_version_below_applied_history_rejected(self):
        with self.assertRaises(ValidationError):
            plan_migrations(SHA, [self.m1, self.m2], [self.m2.as_dict()], ledger_present=True)

    def test_unknown_applied_migration_in_known_schema_range_rejected(self):
        unknown = MigrationIdentity(1, "not_known", "b" * 64, 0)
        with self.assertRaises(ValidationError):
            plan_migrations(SHA, [self.m2], [unknown.as_dict()], ledger_present=True)

    def test_future_compatible_ledger_allows_old_binary(self):
        future = MigrationIdentity(3, "next", "f" * 64, 0)
        plan = plan_migrations(SHA, [self.m1, self.m2], [self.m1.as_dict(), self.m2.as_dict(), future.as_dict()], ledger_present=True)
        self.assertEqual(plan.pending, ())

    def test_future_incompatible_ledger_blocks_rollback(self):
        future = MigrationIdentity(3, "breaking", "f" * 64, 3)
        with self.assertRaises(ValidationError):
            plan_migrations(SHA, [self.m1, self.m2], [self.m1.as_dict(), self.m2.as_dict(), future.as_dict()], ledger_present=True)

    def test_ledger_rejects_null_floor_duplicates_and_bogus_types(self):
        for bad in (None, True, "0", -1, 2):
            with self.subTest(floor=bad):
                row = self.m1.as_dict()
                row["min_compatible_binary_version"] = bad
                with self.assertRaises(ValidationError):
                    plan_migrations(SHA, [self.m1], [row], ledger_present=True)
        with self.assertRaises(ValidationError):
            plan_migrations(SHA, [], [self.m1.as_dict(), self.m1.as_dict()], ledger_present=True)

    def test_approval_exact_sha_plan_and_count(self):
        plan = plan_migrations(SHA, [self.m1], [], ledger_present=True)
        decision = dict(id="decision-1", state="approved", sha=SHA,
                        plan_fingerprint=plan.plan_fingerprint, required_migration_count=1)
        self.assertEqual(require_exact_approval(plan, [decision]), "decision-1")
        for change in (dict(sha=B), dict(plan_fingerprint="f" * 64),
                       dict(state="revoked"), dict(required_migration_count=2)):
            with self.subTest(change=change):
                with self.assertRaises(ValidationError):
                    require_exact_approval(plan, [decision | change])

    def test_reader_verifies_file_bytes_and_directive(self):
        with tempfile.TemporaryDirectory() as d:
            data = b"-- evcowbbe:min-compatible-binary-version=0\nCREATE TABLE x(y INT);\n"
            (Path(d) / "000001_create_users.sql").write_bytes(data)
            m, = read_source_migrations(d)
            self.assertEqual(m, MigrationIdentity(1, "create_users", hashlib.sha256(data).hexdigest(), 0))

    def test_reader_rejects_malformed_and_duplicate_directives(self):
        with tempfile.TemporaryDirectory() as d:
            sql = Path(d) / "000001_x.sql"
            for content in (
                "SELECT 1;\n", "-- evcowbbe:min-compatible-binary-version=1\n-- evcowbbe:min-compatible-binary-version=1\n",
                "-- evcowbbe:min-compatible-binary-version=2\n", "-- evcowbbe:min-compatible-binary-version=0 xyz\n",
            ):
                with self.subTest(sql=content):
                    sql.write_text(content)
                    with self.assertRaises(ValidationError):
                        read_source_migrations(d)


class PreflightCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = ControlPlane(Path(self.temp.name) / "state.sqlite3")
        self.control.initialize()
        self.authority = GitHubAuthority(lambda path:
            {"commit": {"sha": SHA}} if path.endswith("/branches/prod")
            else {"workflow_runs": [positive_run()]})

    def test_no_admission_blocks_without_runs_or_mutation(self):
        outcome = evaluate_preflight(self.authority, self.control, [], [], ledger_present=True)
        self.assertEqual(outcome.state, "blocked")
        self.assertEqual(outcome.reason, "authoritative_prod_head_not_admitted")
        self.assertEqual(self.control.status(verbose=True)["runs"], [])

    def test_exact_ingress_preflight_with_no_migrations_is_ready_and_read_only(self):
        self.control.submit_request(dict(event="push", sha=SHA, branch="prod", actor="ci", source_id="ci:1"))
        initial_count = len(self.control.history())
        outcome = evaluate_preflight(self.authority, self.control, [], [], ledger_present=True)
        self.assertEqual(outcome.state, "ready")
        self.assertIsNotNone(outcome.request_id)
        self.assertIsNone(outcome.migration_decision_id)
        self.assertEqual(len(self.control.history()), initial_count)
        self.assertFalse(outcome.as_dict()["mutated"])

    def test_pending_migration_requires_exact_durable_approval(self):
        self.control.submit_request(dict(event="push", sha=SHA, branch="prod", actor="ci", source_id="ci:1"))
        m = MigrationIdentity(1, "create_users", "1" * 64, 0)
        waiting = evaluate_preflight(self.authority, self.control, [m], [], ledger_present=True)
        self.assertEqual((waiting.state, waiting.reason), ("blocked", "migration_approval_required"))
        self.control.approve_migration(SHA, waiting.plan.plan_fingerprint, "admin", "reviewed-plan", 1)
        accepted = evaluate_preflight(self.authority, self.control, [m], [], ledger_present=True)
        self.assertEqual(accepted.state, "ready")
        self.assertIsNotNone(accepted.migration_decision_id)
        self.assertFalse(self.control.status(verbose=True)["runs"])

    def test_pause_blocks_without_approval_bypass(self):
        self.control.submit_request(dict(event="push", sha=SHA, branch="prod", actor="ci", source_id="ci:1"))
        self.control.pause("admin", "maintenance")
        blocked = evaluate_preflight(self.authority, self.control, [], [], ledger_present=True)
        self.assertEqual((blocked.state, blocked.reason), ("blocked", "paused"))

class PreflightCLITest(unittest.TestCase):
    def test_snapshot_parser_accepts_machine_schema(self):
        from ops.deploy.executor.cli import read_ledger_snapshot
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snapshot.json"
            path.write_text(json.dumps({"ledger_present": True, "applied": []}))
            self.assertEqual(read_ledger_snapshot(path), (True, []))

    def test_snapshot_parser_rejects_text_status_and_unknown_fields(self):
        from ops.deploy.executor.cli import read_ledger_snapshot
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snapshot.json"
            for text in ('ledger_present=true known_applied=0',
                         json.dumps({"ledger_present": "true", "applied": []}),
                         json.dumps({"ledger_present": True, "applied": [], "unknown": 1})):
                with self.subTest(text=text):
                    path.write_text(text)
                    with self.assertRaises(ValidationError):
                        read_ledger_snapshot(path)

    def test_token_reader_rejects_symlink_and_group_world_access(self):
        from ops.deploy.executor.cli import read_token_file
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "token"
            path.write_text("test-token")
            path.chmod(0o600)
            self.assertEqual(read_token_file(path), "test-token")
            path.chmod(0o644)
            if __import__("os").name == "posix":
                with self.assertRaises(ValidationError):
                    read_token_file(path)
            symlink = Path(d) / "link"
            try:
                symlink.symlink_to(path)
            except OSError:
                # Standard, non-elevated Windows accounts may lack symlink rights.
                if __import__("os").name != "nt":
                    raise
            else:
                with self.assertRaises(ValidationError):
                    read_token_file(symlink)

    def test_snapshot_parser_rejects_duplicate_json_properties(self):
        from ops.deploy.executor.cli import read_ledger_snapshot
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snapshot.json"
            path.write_text('{"ledger_present":true,"applied":[],"applied":[]}')
            with self.assertRaises(ValidationError):
                read_ledger_snapshot(path)
