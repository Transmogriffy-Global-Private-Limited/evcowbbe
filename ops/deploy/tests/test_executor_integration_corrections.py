"""Regression tests for immutable baseline ledger reads and late operator fences."""
from __future__ import annotations

import json
import stat
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.deploy.control_plane import ControlPlane
from ops.deploy.executor.host.operations import HostOperationFailed, VPS2Operations
from ops.deploy.executor.host import contract as c

A, OLD = 'a' * 40, 'c' * 40


class LateOperatorFenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cp = ControlPlane(str(Path(temporary.name) / 'state.sqlite3'))
        admitted = self.cp.submit_request({
            'event': 'push', 'branch': 'prod', 'sha': A,
            'actor': 'source-ci', 'source_id': 'ci:late-fence:1',
        })['request']
        run = self.cp.create_run(admitted['id'], A, 'trusted-executor')
        self.run_id = run['id']
        self.cp.claim_run_execution(self.run_id, A, 'trusted-executor')
        self.cp.checkpoint_run(self.run_id, 'build', 'intent', 'trusted-executor', sha=A)
        self.ops = VPS2Operations(runner=lambda *_a, **_kw: self.fail('no subprocess expected'))
        self.mock_control = patch('ops.deploy.executor.host.operations.ControlPlane', return_value=self.cp)
        self.mock_control.start()
        self.addCleanup(self.mock_control.stop)

    def test_uncontrolled_stage_is_eligible_before_an_operator_changes_state(self):
        self.ops._require_gate(A, 'build')

    def test_pause_after_durable_intent_blocks_root_build(self):
        self.cp.pause('operator', 'maintenance')
        with self.assertRaises(HostOperationFailed):
            self.ops._require_gate(A, 'build')

    def test_sideline_after_durable_intent_blocks_root_build(self):
        self.cp.sideline(A, 'operator', 'hold this commit')
        with self.assertRaises(HostOperationFailed):
            self.ops._require_gate(A, 'build')

    def test_rollback_hold_after_durable_intent_blocks_root_build(self):
        self.cp.set_rollback_hold(OLD, 'operator', 'retain prior runtime')
        with self.assertRaises(HostOperationFailed):
            self.ops._require_gate(A, 'build')

    def test_activation_intent_cannot_bypass_new_pause(self):
        self.cp.checkpoint_run(self.run_id, 'build', 'observed', 'trusted-executor', sha=A)
        self.cp.checkpoint_run(self.run_id, 'activation', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.ops._require_gate(A, 'activation')
        self.cp.pause('operator', 'stop before switching')
        with self.assertRaises(HostOperationFailed):
            self.ops._require_gate(A, 'activation')

    def test_rollback_requires_matching_durable_hold_and_intent(self):
        self.cp.checkpoint_run(self.run_id, 'build', 'observed', 'trusted-executor', sha=A)
        self.cp.checkpoint_run(self.run_id, 'activation', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(self.run_id, 'activation', 'observed', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(self.run_id, 'runtime_verification', 'intent', 'trusted-executor', sha=A)
        self.cp.set_rollback_hold(OLD, 'operator', 'runtime unhealthy')
        with self.assertRaises(HostOperationFailed):
            self.ops._require_gate(OLD, 'rollback')
        self.cp.checkpoint_run(self.run_id, 'rollback', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.ops._require_gate(OLD, 'rollback')
        self.cp.pause('operator', 'hold forward work')
        # The paused system must still be able to complete an already
        # authorized safe rollback, rather than lock itself into the bad SHA.
        self.ops._require_gate(OLD, 'rollback')


class LedgerObserverBootstrapTests(unittest.TestCase):
    def test_version_independent_ledger_does_not_invoke_active_migrator(self):
        calls = []
        expected = {'ledger_present': True, 'applied': []}
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return (json.dumps(expected) + '\n').encode('ascii')
        ops = VPS2Operations(runner=runner)
        with patch('pathlib.Path.stat', return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o555, st_uid=0)), patch.object(ops, '_migrator', side_effect=AssertionError('never use active baseline migrator')):
            self.assertEqual(ops.ledger(), expected)
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], '/usr/bin/systemd-run')
        self.assertIn('--uid=evcowbbe', argv)
        self.assertIn('--gid=evcowbbe', argv)
        self.assertIn(f'--property=EnvironmentFile={c.APP_ENV}', argv)
        self.assertEqual(argv[-1], '/opt/evcowbbe-deploy/current/bin/evcowbbe-ledger-observer')
        self.assertNotIn('/srv/evcowbbe/current', ' '.join(argv))
        self.assertNotIn('status', ' '.join(argv))

    def test_ledger_rejects_conflicting_duplicate_json(self):
        ops = VPS2Operations(runner=lambda *_a, **_kw: b'{"ledger_present":true,"ledger_present":false,"applied":[]}\n')
        with patch('pathlib.Path.stat', return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o555, st_uid=0)), self.assertRaises(HostOperationFailed):
            ops.ledger()

    def test_upgrade_builds_observer_from_exact_git_archive_without_network(self):
        script = Path('ops/deploy/vps/upgrade-control-plane.sh').read_text()
        self.assertIn('cmd/ledger-observer internal/database go.mod go.sum', script)
        self.assertIn('GOPROXY=off', script)
        self.assertIn('GOTOOLCHAIN=local', script)
        self.assertIn('go build -trimpath -buildvcs=false', script)
        self.assertNotIn('systemctl restart evcowbbe-dev.service', script)


if __name__ == '__main__':
    unittest.main()
