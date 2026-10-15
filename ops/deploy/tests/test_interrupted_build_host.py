"""Source-only negative tests. Never inspect or alter a real VPS."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.deploy.vps import interrupted_build_host as h
from ops.deploy.executor.build_recovery import REQUIRED_HOST_FACTS

A = 'a'*40
B = 'b'*40


def fake_systemctl(*args):
    if args[0] == 'show':
        return (0, 'ControlPID=0\nSubState=failed\nMainPID=0\nActiveState=failed')
    if args[0] == 'is-enabled':
        return (1, 'disabled')
    if args[0] == 'is-active':
        return (3, 'inactive')
    raise AssertionError(args)


class HostRecoveryObservationTests(unittest.TestCase):
    def test_quiescent_exactly_and_fail_closed_on_live_units(self):
        h._quiescent(fake_systemctl)
        for broken in (
            'ControlPID=2\nSubState=failed\nMainPID=0\nActiveState=failed',
            'ControlPID=0\nSubState=failed\nMainPID=1\nActiveState=failed',
            'ControlPID=0\nSubState=running\nMainPID=0\nActiveState=active',
            'ControlPID=0\nSubState=failed\nMainPID=0\nActiveState=failed\nControlPID=0',
            'ControlPID=0\nMainPID=0\nActiveState=failed',
        ):
            with self.subTest(broken=broken), self.assertRaises(h.HostRecoveryBlocked):
                h._quiescent(lambda *args: (0, broken) if args[0]=='show' else fake_systemctl(*args))
        for which, changed in (('is-enabled', (0, 'enabled')), ('is-active', (0, 'active'))):
            with self.subTest(which=which), self.assertRaises(h.HostRecoveryBlocked):
                h._quiescent(lambda *args: changed if args[0]==which else fake_systemctl(*args))

    def test_lock_path_is_exact_executor_lock_path(self):
        if not Path('ops/deploy/vps/execute_once.py').is_file():
            self.skipTest('Full repository executor source unavailable in isolated fixture')
        from ops.deploy.vps.execute_once import LOCK
        self.assertEqual(h.LOCK, LOCK)
        self.assertEqual(h.LOCK.as_posix(), '/opt/evcowbbe-deploy/executor.lock')

    def test_no_pretense_of_sql_or_retry_authorization(self):
        self.assertIn('postgresql_ledger_matches_prebuild_baseline', REQUIRED_HOST_FACTS)
        self.assertNotIn('postgresql_ledger_matches_prebuild_baseline', h.FencedHostObservation.__dataclass_fields__)
        self.assertEqual(h.FencedHostObservation(A, B).result,
                         'host_quiescence_only_not_authorized_to_reconcile')

    @unittest.skipUnless(os.name=='posix' and hasattr(os, 'geteuid'), 'POSIX-only host fixture')
    def test_positive_context_keeps_fd_open_and_closes_afterward(self):
        class GoodHost:
            def active(self):
                return B
            def verify(self, value):
                return value == B
        with tempfile.TemporaryDirectory() as d:
            fd = os.open(Path(d)/'test.lock', os.O_RDWR | os.O_CREAT, 0o600)
            seen = []
            def check_process(value):
                seen.append(('process', value))
            def check_artifact(value, uid):
                seen.append(('artifact', value, uid))
            with patch.object(h.os, 'geteuid', return_value=0):
                with h.fenced_build_observation(
                    A, B, operations_factory=GoodHost,
                    lock_acquire=lambda: fd, systemctl=fake_systemctl,
                    processes=check_process, artifacts=check_artifact,
                    builder_uid=lambda: 1234,
                ) as observed:
                    self.assertEqual(observed.candidate_sha, A)
                    self.assertEqual(observed.previous_sha, B)
                    os.fstat(fd)  # Still held inside context.
            self.assertEqual(len(seen), 4)
            with self.assertRaises(OSError):
                os.fstat(fd)

    @unittest.skipUnless(os.name=='posix', 'POSIX-only host fixture')
    def test_runtime_mismatch_and_active_worker_block_and_release_fd(self):
        class WrongHost:
            def active(self):
                return A
            def verify(self, _):
                return True
        class GoodHost:
            def active(self):
                return B
            def verify(self, _):
                return True
        for host, process in ((WrongHost, lambda sha: None),
                              (GoodHost, lambda sha: (_ for _ in ()).throw(h.HostRecoveryBlocked('worker')))):
            with self.subTest(host=host):
                with tempfile.TemporaryDirectory() as d:
                    fd = os.open(Path(d)/'lock', os.O_CREAT | os.O_RDONLY, 0o444)
                    with patch.object(h.os, 'geteuid', return_value=0):
                        with self.assertRaises(h.HostRecoveryBlocked):
                            with h.fenced_build_observation(
                                A, B, operations_factory=host,
                                lock_acquire=lambda: fd, systemctl=fake_systemctl,
                                processes=process, artifacts=lambda *_: None,
                                builder_uid=lambda: 12,
                            ):
                                self.fail('Unsafe observation yielded')
                    with self.assertRaises(OSError):
                        os.fstat(fd)

    @unittest.skipUnless(os.name=='posix', 'POSIX-only host fixture')
    def test_candidate_artifact_and_dangling_symlink_block(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            work, prepared, releases = root/'build-work', root/'build-work'/'prepared', root/'releases'
            for path in (work, prepared, releases):
                path.mkdir(parents=True, exist_ok=True)
            owner = os.geteuid()
            with patch.object(h, 'APP_ROOT', root), patch.object(h, 'WORK', work), patch.object(h, 'RELEASES', releases):
                h._no_artifacts(A, owner)
                for path in (prepared/A, work/('.build-'+A+'-xyz'), releases/('.staging-'+A+'-xyz')):
                    path.symlink_to(root/'does-not-exist')
                    try:
                        with self.assertRaises(h.HostRecoveryBlocked):
                            h._no_artifacts(A, owner)
                    finally:
                        path.unlink()
                (prepared/'prepared-elsewhere').mkdir(mode=0o700)
                h._no_artifacts(A, owner)

    @unittest.skipUnless(os.name=='posix', 'POSIX-only host fixture')
    def test_procfs_scanner_rejects_live_builder_and_executor_cgroup(self):
        with tempfile.TemporaryDirectory() as d:
            proc = Path(d)
            pid = proc/'200'
            pid.mkdir()
            (pid/'cmdline').write_bytes(b'/usr/bin/go\0build\0')
            (pid/'cgroup').write_bytes(b'0::/system.slice/other.service\n')
            (pid/'cwd').symlink_to(proc)
            with patch.object(h, 'PROC', proc):
                h._no_builder_processes(A)
                (pid/'cmdline').write_bytes(b'/bin/bash\0/opt/evcowbbe-deploy/current/bin/evcowbbe-build-sha\0')
                with self.assertRaises(h.HostRecoveryBlocked):
                    h._no_builder_processes(A)
                (pid/'cmdline').write_bytes(b'/usr/bin/go\0build\0')
                (pid/'cgroup').write_bytes(b'0::/system.slice/evcowbbe-deploy-executor.service\n')
                with self.assertRaises(h.HostRecoveryBlocked):
                    h._no_builder_processes(A)
                (pid/'cgroup').write_bytes(b'0::/system.slice/other.service\n')
                (pid/'cwd').unlink()
                (pid/'cwd').symlink_to(proc/('.build-'+A+'-xyz')/'source')
                with self.assertRaises(h.HostRecoveryBlocked):
                    h._no_builder_processes(A)

    @unittest.skipUnless(os.name=='posix', 'POSIX-only host fixture')
    def test_exclusive_kernel_lock_rejects_second_observer(self):
        with tempfile.TemporaryDirectory() as d:
            parent = Path(d)
            lock = parent/'executor.lock'
            lock.write_bytes(b'')
            lock.chmod(0o444)
            owner = os.geteuid()
            if owner != 0:
                self.skipTest('needs root-owned lock for real inode test')
            with patch.object(h, 'LOCK', lock):
                fd = h._lock_executor()
                try:
                    with self.assertRaises(h.HostRecoveryBlocked):
                        h._lock_executor()
                finally:
                    os.close(fd)
                fd = h._lock_executor()
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
