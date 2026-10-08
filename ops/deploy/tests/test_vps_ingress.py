import io
import json
import multiprocessing
import subprocess
import sys
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ops.deploy.control_plane import ControlPlane, IdempotencyConflict, ValidationError
from ops.deploy.vps import admit, admit_and_wake, reconcile, ssh_ingress
from ops.deploy.vps.common import ALLOWED_SSH_COMMAND, MAX_REQUEST_BYTES, encode_json, parse_exact_request


SHA_A = "a" * 40
SHA_B = "b" * 40


def payload(sha: str = SHA_A, source_id: str = "source-ci:1:1") -> bytes:
    return json.dumps(
        {"event": "push", "sha": sha, "branch": "prod", "actor": "github-actions", "source_id": source_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def concurrent_admit(state_db: str, raw: bytes, result_queue: multiprocessing.Queue) -> None:
    try:
        result_queue.put(("ok", json.loads(admit.admit(raw, state_db))["created"]))
    except Exception as exc:  # pragma: no cover - asserted by parent process.
        result_queue.put(("error", type(exc).__name__))


class VPSIngressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.state_db = str(Path(self.temporary_directory.name) / "orchestrator.sqlite3")

    def test_exact_bounded_request_parser_rejects_ambiguity(self) -> None:
        self.assertEqual(parse_exact_request(payload())["sha"], SHA_A)
        self.assertEqual(parse_exact_request(payload() + b"\n")["sha"], SHA_A)
        for trailing in (b"\n\n", b"\r\n", b" ", b"{}", b"\x00"):
            with self.assertRaises(ValidationError):
                parse_exact_request(payload() + trailing)
        with self.assertRaises(ValidationError):
            parse_exact_request(payload().replace(b'"actor":"github-actions"', b'"actor":"first","actor":"github-actions"'))
        with self.assertRaises(ValidationError):
            parse_exact_request(b"{" + b"x" * MAX_REQUEST_BYTES)
        with self.assertRaises(ValidationError):
            parse_exact_request(json.dumps({"event": "push"}).encode("ascii"))
        with self.assertRaises(ValidationError):
            parse_exact_request(payload().replace(b"github-actions", b"bad\nactor"))

    def test_forced_command_is_exact_and_forwarding_never_interprets_json(self) -> None:
        ssh_ingress.validate_original_command({"SSH_ORIGINAL_COMMAND": ALLOWED_SSH_COMMAND})
        with self.assertRaises(ValidationError):
            ssh_ingress.validate_original_command({"SSH_ORIGINAL_COMMAND": "anything else"})
        seen: list[bytes] = []

        def runner(raw: bytes) -> tuple[int, bytes, bytes]:
            seen.append(raw)
            return 0, encode_json({"accepted": True, "created": True, "request_id": "a" * 32, "sha": SHA_A}), b""

        self.assertEqual(ssh_ingress.forward(payload(), runner), runner(payload())[1])
        self.assertEqual(seen[0], payload())
        with self.assertRaises(Exception):
            ssh_ingress.forward(payload(), lambda _raw: (1, b"", b"failure"))

    def test_admission_is_idempotent_committed_before_ack_and_conflicts_are_rejected(self) -> None:
        first = json.loads(admit.admit(payload(), self.state_db))
        self.assertTrue(first["accepted"])
        self.assertTrue(first["created"])
        self.assertEqual(len(ControlPlane(self.state_db).list_requests()), 1)
        second = json.loads(admit.admit(payload(), self.state_db))
        self.assertFalse(second["created"])
        conflicting = payload(SHA_B, "source-ci:1:1")
        with self.assertRaises(IdempotencyConflict):
            admit.admit(conflicting, self.state_db)
        self.assertEqual(len(ControlPlane(self.state_db).list_requests()), 1)

    def test_failure_before_commit_and_passive_recovery_inspection(self) -> None:
        with self.assertRaises(ValidationError):
            admit.admit(b"not-json", self.state_db)
        self.assertFalse(Path(self.state_db).exists())
        admit.admit(payload(), self.state_db)
        report = reconcile.inspect_pending(self.state_db)
        self.assertEqual(report, {"mode": "passive", "accepted_requests_total": 1, "reconciliation_state": "not_evaluated", "deployed": False})
        self.assertEqual(ControlPlane(self.state_db).desired_state()["reason"], "authoritative_prod_head_required")
        admit.admit(payload(SHA_B, "source-ci:2:1"), self.state_db)
        self.assertEqual(ControlPlane(self.state_db).desired_state(SHA_B)["request"]["sha"], SHA_B)

    def test_concurrent_ingress_has_one_durable_request(self) -> None:
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [context.Process(target=concurrent_admit, args=(self.state_db, payload(), queue)) for _ in range(2)]
        for process in processes:
            process.start()
        results = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(results), [("ok", False), ("ok", True)])
        self.assertEqual(len(ControlPlane(self.state_db).list_requests()), 1)

    def test_wake_failure_after_commit_is_nonzero_and_recovery_keeps_request(self) -> None:
        calls: list[list[str]] = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            if len(calls) == 1:
                return SimpleNamespace(returncode=0, stdout=admit.admit(kwargs["input"], self.state_db), stderr=b"")
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"wake failure")

        status, response = admit_and_wake.run(payload(), runner)
        self.assertEqual(status, 5)
        self.assertEqual(json.loads(response)["error"], "wake_failed")
        self.assertEqual(len(ControlPlane(self.state_db).list_requests()), 1)
        self.assertEqual(calls[1], ["/usr/bin/systemctl", "start", admit_and_wake.RECONCILE_UNIT])

    def test_workflow_is_privileged_only_for_valid_successful_prod_source_ci(self) -> None:
        workflow = Path(".github/workflows/prod-vps-ingest.yml").read_text(encoding="utf-8")
        for required in (
            "workflow_run:",
            'workflows: ["Source CI"]',
            "conclusion == 'success'",
            "event == 'push'",
            "head_branch == 'prod'",
            "head_sha",
            "StrictHostKeyChecking=yes",
            "evcowbbe-deploy-ingest-v1",
        ):
            self.assertIn(required, workflow)
        for forbidden in ("actions/checkout", "upload-artifact", "download-artifact", "StrictHostKeyChecking=no", "ssh-keyscan", "pull_request_target"):
            self.assertNotIn(forbidden, workflow)

    def test_invalid_ssh_ingress_exits_nonzero_without_handoff(self) -> None:
        stderr = io.StringIO()
        exit_code = ssh_ingress.main(
            stdin=io.BytesIO(payload()),
            stdout=io.StringIO(),
            stderr=stderr,
            environment={"SSH_ORIGINAL_COMMAND": "shell"},
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(json.loads(stderr.getvalue())["error"], "ingress_failed")

    def test_real_admission_entrypoint_in_subprocess_and_idempotency(self) -> None:
        code = (
            "import sys; from ops.deploy.vps.admit import main; "
            "raise SystemExit(main(state_db=sys.argv[1]))"
        )
        for expected_created in (True, False):
            process = subprocess.run(
                [sys.executable, "-B", "-c", code, self.state_db],
                input=payload() + b"\n", stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["created"], expected_created)
            self.assertEqual(result["sha"], SHA_A)
            self.assertEqual(len(ControlPlane(self.state_db).list_requests()), 1)

    def test_real_admission_entrypoint_rejects_duplicate_keys(self) -> None:
        code = (
            "import sys; from ops.deploy.vps.admit import main; "
            "raise SystemExit(main(state_db=sys.argv[1]))"
        )
        double_key = payload().replace(
            b'"actor":"github-actions"', b'"actor":"impersonated","actor":"github-actions"'
        )
        process = subprocess.run(
            [sys.executable, "-B", "-c", code, self.state_db],
            input=double_key + b"\n", capture_output=True, timeout=15,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stderr)["accepted"], False)
        self.assertFalse(Path(self.state_db).exists())

    def test_ssh_ack_sha_binding_and_double_newline_rejection(self) -> None:
        def wrong_sha_runner(raw: bytes) -> tuple[int, bytes, bytes]:
            return 0, encode_json({"accepted": True, "created": True, "request_id": "a" * 32, "sha": SHA_B}), b""
        with self.assertRaises(ValidationError):
            ssh_ingress.forward(payload() + b"\n", wrong_sha_runner)
        with self.assertRaises(ValidationError):
            parse_exact_request(payload() + b"\n\n")

    def test_exact_historical_admissions_count_is_not_capped(self) -> None:
        control = ControlPlane(self.state_db)
        control.initialize()
        # Add enough rows to cross the original arbitrary list_requests(1000) cap.
        for index in range(1005):
            control.submit_request({
                "event": "push", "sha": SHA_A, "branch": "prod",
                "actor": "github-actions", "source_id": f"source-ci:hist-{index}:1",
            })
        report = reconcile.inspect_pending(self.state_db)
        self.assertEqual(report["accepted_requests_total"], 1005)
        self.assertEqual(report["reconciliation_state"], "not_evaluated")

    def test_installer_uses_git_archive_and_root_owned_restricted_key(self) -> None:
        installer = Path("ops/deploy/vps/install-control-plane.sh").read_text(encoding="utf-8")
        self.assertIn('git -C "$source_dir" archive', installer)
        self.assertIn('status --porcelain --untracked-files=all', installer)
        self.assertIn('restrict,command=', installer)
        self.assertIn('chown root:root "$authorized_keys"', installer)
        self.assertNotIn('sshd_dropin=', installer)
        self.assertLess(installer.index('systemctl enable --now'), installer.index('authorized_line='))
        self.assertIn('source SHA must be 40 lowercase hexadecimal characters', installer)
        self.assertIn('state directory must be owned by evcow-orchestrator', installer)

    def test_installation_artifacts_are_pinned_and_do_not_touch_application_release_paths(self) -> None:
        installer = Path("ops/deploy/vps/install-control-plane.sh").read_text(encoding="utf-8")
        self.assertIn("source SHA must be 40 lowercase", installer)
        self.assertIn("/opt/evcowbbe-deploy", installer)
        self.assertNotIn("systemctl restart evcowbbe-dev", installer)
        self.assertIn("application service and /srv/evcowbbe/current were not touched", installer)
        self.assertIn("existing trigger authorized_keys was not modified", installer)
        self.assertIn("sshd -t", installer)
        self.assertIn("visudo -cf", installer)
        service = Path("ops/deploy/vps/systemd/evcowbbe-deploy-reconcile.service").read_text(encoding="utf-8")
        self.assertIn("User=evcow-orchestrator", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertNotIn("evcowbbe-dev.service", service)


if __name__ == "__main__":
    unittest.main()
