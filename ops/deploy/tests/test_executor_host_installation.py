"""Patch 8 strict authenticated authority, arming, process fence and recovery."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ops.deploy.control_plane import ValidationError
from ops.deploy.executor.authority import AuthorityUnavailable, VerifiedAuthority
from ops.deploy.executor.host.contract import decode_request
from ops.deploy.executor.host.operations import VPS2Operations, HostOperationFailed
from ops.deploy.executor.host.port import VPS2ExecutionPort, VPS2VerifiedAuthority
from ops.deploy.vps import execute_once as exec_vps
from ops.deploy.vps import recovery_inspect

A, B = 'a' * 40, 'b' * 40


class VerifiedRootAuthorityTests(unittest.TestCase):
    def test_request_allows_only_current_or_valid_expected_sha(self):
        self.assertEqual(decode_request(b'{"op":"authority","sha":null}\n'), ('authority', None))
        self.assertEqual(decode_request(b'{"op":"authority","sha":"'+A.encode()+b'"}'), ('authority', A))
        for raw in (b'{"op":"authority","sha":1}',
                    b'{"op":"authority","sha":"HEAD"}',
                    b'{"op":"authority","sha":null,"token":"x"}',
                    b'{"op":"active","sha":"'+A.encode()+b'"}'):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                decode_request(raw)

    def test_root_authority_returns_safe_schema_only(self):
        ops = VPS2Operations(runner=lambda *_args, **_kw: self.fail('no subprocess allowed'))
        verified = VerifiedAuthority(A, 47, 2)
        with patch.object(ops, '_github_authorize', return_value=verified) as guard:
            self.assertEqual(ops.authority(A), {
                'sha': A, 'ci_run_id': 47, 'ci_attempt': 2, 'branch': 'prod',
            })
            guard.assert_called_once_with(A)

    def test_credential_missing_fails_without_authentication_bypass(self):
        ops = VPS2Operations()
        with patch('ops.deploy.executor.host.contract.GITHUB_TOKEN', Path('/nonexistent/test-token')):
            with self.assertRaises(HostOperationFailed):
                ops.authority(None)

    def test_verified_client_rejects_invalid_ack_and_wrong_exact_sha(self):
        good = {'sha': A, 'branch': 'prod', 'ci_run_id': 123, 'ci_attempt': 1}
        for value in ({**good, 'token': 'secret'}, {**good, 'sha': B},
                      {**good, 'ci_attempt': True}, {**good, 'branch': 'main'},
                      {**good, 'ci_run_id': 0}, {'sha': A}):
            with self.subTest(value=value):
                runner = lambda _argv, **_kwargs: SimpleNamespace(
                    returncode=0, stdout=json.dumps({'ok': True, 'result': value}).encode('ascii'))
                with self.assertRaises(AuthorityUnavailable):
                    VPS2VerifiedAuthority(VPS2ExecutionPort(runner=runner)).verify(A)

    def test_verified_client_resolves_sha_and_sanitizes_request(self):
        calls = []
        payload = {'sha': A, 'branch': 'prod', 'ci_run_id': 123, 'ci_attempt': 1}
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'result': payload}).encode('ascii'))
        port = VPS2ExecutionPort(runner=runner)
        authority = VPS2VerifiedAuthority(port)
        self.assertEqual(authority.verify(A), VerifiedAuthority(A, 123, 1))
        self.assertEqual(authority.verify(), VerifiedAuthority(A, 123, 1))
        self.assertEqual(json.loads(calls[0][1]['input']), {'op': 'authority', 'sha': A})
        self.assertEqual(json.loads(calls[1][1]['input']), {'op': 'authority', 'sha': None})
        self.assertEqual(calls[0][0][:2], ['/usr/bin/sudo', '-n'])


class CoordinatorAuthorityIntegrationTests(unittest.TestCase):
    def test_exact_safe_bridge_wiring_already_current_never_creates_run(self):
        from ops.deploy.control_plane import ControlPlane
        from ops.deploy.executor.lifecycle import DeploymentLifecycle

        with tempfile.TemporaryDirectory() as temp:
            cp = ControlPlane(str(Path(temp) / 'state.sqlite3'))
            cp.initialize()
            cp.submit_request({
                'event': 'push', 'branch': 'prod', 'sha': A,
                'actor': 'source-ci', 'source_id': 'verified-bridge:1',
            })
            calls = []
            def runner(argv, **kwargs):
                self.assertEqual(argv[:2], ['/usr/bin/sudo', '-n'])
                request = json.loads(kwargs['input'])
                calls.append(request)
                op = request['op']
                self.assertIn(op, {'authority', 'source', 'ledger', 'active', 'verify'})
                results = {
                    'authority': {'sha': A, 'branch': 'prod', 'ci_run_id': 100, 'ci_attempt': 1},
                    'source': [], 'ledger': {'ledger_present': True, 'applied': []},
                    'active': A, 'verify': True,
                }
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    'ok': True, 'result': results[op],
                }).encode('ascii'))

            port = VPS2ExecutionPort(runner=runner)
            lifecycle = DeploymentLifecycle(VPS2VerifiedAuthority(port), cp, port)
            result = lifecycle.execute_once()
            self.assertEqual(result.state, 'already_current')
            self.assertEqual(cp.status(verbose=True)['runs'], [])
            self.assertIn('authority', [x['op'] for x in calls])
            self.assertIn('ledger', [x['op'] for x in calls])
            self.assertIn('verify', [x['op'] for x in calls])



@unittest.skipUnless(os.name == 'posix', 'requires POSIX filesystem and flock')
class ExecutorArmTests(unittest.TestCase):
    def test_arm_exact_control_release_and_readonly_root_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / 'releases'
            releases.mkdir()
            (releases / A).mkdir()
            control = root / 'current'
            control.symlink_to('releases/' + A)
            arm = root / 'arm'
            arm.write_text(f'evcowbbe-executor-v1 {A}\n')
            arm.chmod(0o444)
            if os.geteuid() != 0:
                self.skipTest('requires root-owned test arm')
            exec_vps.require_arm(control, arm)
            arm.write_text(f'evcowbbe-executor-v1 {B}\n')
            with self.assertRaises(exec_vps.ExecutorDisabled):
                exec_vps.require_arm(control, arm)
            arm.write_text(f'evcowbbe-executor-v1 {A}\n')
            control.unlink()
            control.symlink_to('releases/' + B)
            with self.assertRaises(exec_vps.ExecutorDisabled):
                exec_vps.require_arm(control, arm)
            arm.unlink()
            with self.assertRaises(exec_vps.ExecutorDisabled):
                exec_vps.require_arm(control, arm)

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'root owned fixture')
    def test_root_owned_readonly_lock_serializes_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.chmod(0o755)
            lock = root / 'executor.lock'
            lock.write_bytes(b'')
            lock.chmod(0o444)
            first = exec_vps.exclusive_lock(lock)
            try:
                with self.assertRaises(BlockingIOError):
                    exec_vps.exclusive_lock(lock)
                assert lock.is_file()
            finally:
                os.close(first)
            second = exec_vps.exclusive_lock(lock)
            os.close(second)
            lock.chmod(0o644)
            with self.assertRaises(exec_vps.ExecutorDisabled):
                exec_vps.exclusive_lock(lock)
            lock.unlink()
            lock.symlink_to('/dev/null')
            with self.assertRaises(OSError):
                exec_vps.exclusive_lock(lock)

    def test_disabled_executor_does_not_construct_port_or_acquire_lock(self):
        with patch('ops.deploy.vps.execute_once.pwd.getpwnam', return_value=SimpleNamespace(pw_uid=os.geteuid())), \
             patch('ops.deploy.vps.execute_once.require_arm', side_effect=exec_vps.ExecutorDisabled()), \
             patch('ops.deploy.vps.execute_once.exclusive_lock') as lock:
            with self.assertRaises(exec_vps.ExecutorDisabled):
                exec_vps.execute_once()
            lock.assert_not_called()

    def test_lock_released_after_interrupted_lifecycle(self):
        r, w = os.pipe()
        os.close(w)
        lifecycle = SimpleNamespace(execute_once=Mock(side_effect=KeyboardInterrupt()))
        with patch('ops.deploy.vps.execute_once.pwd.getpwnam', return_value=SimpleNamespace(pw_uid=os.geteuid())), \
             patch('ops.deploy.vps.execute_once.require_arm'), \
             patch('ops.deploy.vps.execute_once.exclusive_lock', return_value=r):
            with self.assertRaises(KeyboardInterrupt):
                exec_vps.execute_once(lifecycle_factory=lambda: lifecycle)
        with self.assertRaises(OSError):
            os.fstat(r)


class InstallationContractTests(unittest.TestCase):
    def test_prepare_never_grants_executor_or_enables_timers(self):
        script = Path('ops/deploy/vps/prepare-executor.sh').read_text()
        self.assertIn('systemctl daemon-reload', script)
        self.assertIn('ast.parse', script)
        self.assertNotIn('bash -n "$source/bin/evcowbbe-deploy-execute"', script)
        self.assertNotIn('systemctl enable --now', script)
        self.assertNotIn('install -o root -g root -m 0440 "$source/sudoers/', script)
        self.assertNotIn('printf \'evcowbbe-executor-v1', script)
        self.assertIn('[[ ! -e $dest && ! -L $dest ]]', script)

    def test_arm_requires_exact_credential_and_is_separate_opt_in(self):
        script = Path('ops/deploy/vps/arm-executor.sh').read_text()
        for part in ('github-readonly.token', 'root:600', 'visudo -cf',
                     'executor-arm', 'executor.lock'):
            self.assertIn(part, script)
        self.assertNotIn('systemctl enable --now', script)
        unit = Path('ops/deploy/vps/systemd/evcowbbe-deploy-executor.service').read_text()
        self.assertIn('User=evcow-orchestrator', unit)
        self.assertIn('NoNewPrivileges=no', unit)
        self.assertNotIn('User=root', unit)
        self.assertIn('ConditionPathExists=/opt/evcowbbe-deploy/executor-arm', unit)
        sudoers = Path('ops/deploy/vps/sudoers/evcowbbe-executor').read_text()
        self.assertIn('evcow-orchestrator ALL=(root) NOPASSWD:', sudoers)
        self.assertIn('evcowbbe-deploy-host-op ""', sudoers)
        for dangerous in ('/bin/sh', '/bin/bash', 'ALL=(ALL)', 'evcow-trigger', 'evcow-builder'):
            self.assertNotIn(dangerous, sudoers)

    def test_recovery_observer_safely_rejects_rootless_invocations(self):
        with patch('ops.deploy.vps.recovery_inspect.os.geteuid', return_value=1000, create=True):
            with self.assertRaises(PermissionError):
                recovery_inspect.inspect()


if __name__ == '__main__':
    unittest.main()
