"""Isolated host privilege and framing tests, safe on non-Linux development PCs."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ops.deploy.control_plane import ValidationError
from ops.deploy.executor.host import contract as c
from ops.deploy.executor.host.bridge import main
from ops.deploy.executor.host.operations import VPS2Operations, HostOperationFailed
from ops.deploy.executor.host.port import BRIDGE, HostPortError, VPS2ExecutionPort

A, B = 'a'*40, 'b'*40


class ContractTests(unittest.TestCase):
    def test_strict_request_no_arbitrary_paths_commands_or_extra_fields(self):
        self.assertEqual(c.decode_request(b'{"op":"build","sha":"' + A.encode() + b'"}\n'), ('build', A))
        for raw in (
            b'', b'[]', b'{"op":"build","sha":null}',
            b'{"op":"build","sha":"../../foo"}',
            b'{"op":"shell","sha":"' + A.encode() + b'"}',
            b'{"op":"active","sha":"' + A.encode() + b'"}',
            b'{"op":"ledger","sha":"' + A.encode() + b'"}',
            b'{"op":"build","sha":"' + A.encode() + b'","path":"/etc/passwd"}',
            b'{"op":"build","op":"rollback","sha":"' + A.encode() + b'"}',
            b'X'*4097,
        ):
            with self.subTest(raw=raw[:50]), self.assertRaises(ValidationError):
                c.decode_request(raw)

    def test_revision_replacement_preserves_all_other_secret_bytes(self):
        old = b'# preserved\nDATABASE_URL=postgres://secret\nBUILD_REVISION=' + A.encode() + b'\nPUBLIC_BASE_URL=https://example.org\n'
        new = c.replace_revision(old, B)
        self.assertEqual(new.replace(B.encode(), A.encode()), old)
        self.assertEqual(c.replace_revision(new, B), new)
        for bad in (b'BUILD_REVISION=x\n', b'BUILD_REVISION=' + A.encode() + b'\nBUILD_REVISION=' + A.encode() + b'\n', b'BUILD_REVISION="' + A.encode() + b'"\n', b'KEY=x\n'):
            with self.subTest(bad=bad[:50]), self.assertRaises(ValidationError):
                c.replace_revision(bad, B)

    def test_invalid_active_symlink_does_not_resolve_outside_release_root(self):
        with tempfile.TemporaryDirectory() as temp:
            current = Path(temp)/'current'
            releases = Path(temp)/'releases'
            releases.mkdir()
            with patch.object(c, 'CURRENT', current), patch.object(c, 'RELEASES', releases):
                for target in ('../etc/passwd', 'releases/../bad', '/tmp/' + A):
                    current.unlink(missing_ok=True)
                    try:
                        current.symlink_to(target)
                    except (NotImplementedError, OSError):
                        self.skipTest('symlink unavailable on Windows')
                    with self.assertRaises(ValidationError):
                        c.active_sha()

    def test_release_requires_root_owned_pair_and_v1_checksum(self):
        with tempfile.TemporaryDirectory() as temp:
            releases = Path(temp)
            target = releases/A
            target.mkdir()
            for name in ('evcowbbe','evcowbbe-migrate'):
                (target/name).write_bytes(b'elf-test')
            import hashlib
            manifest = {'format':'evcowbbe-vps2-v1','sha':A,
                        'server_sha256':hashlib.sha256(b'elf-test').hexdigest(),
                        'migrator_sha256':hashlib.sha256(b'elf-test').hexdigest()}
            (target/'RELEASE-MANIFEST').write_text(json.dumps(manifest))
            with patch.object(c, 'RELEASES', releases):
                self.assertFalse(c.verify_release(A)) # not executable
                if os.name == 'posix':
                    (target/'evcowbbe').chmod(0o555)
                    (target/'evcowbbe-migrate').chmod(0o555)
                    if os.geteuid() == 0:
                        self.assertTrue(c.verify_release(A))
                        (target/'evcowbbe').write_bytes(b'changed')
                        self.assertFalse(c.verify_release(A))

    def test_installer_does_not_authorize_executor_or_modify_application(self):
        text = Path('ops/deploy/vps/upgrade-control-plane.sh').read_text(encoding='utf-8')
        self.assertIn('git -C "$source_dir" archive', text)
        self.assertIn('runuser -u evcow-orchestrator', text)
        self.assertNotIn('systemctl restart evcowbbe-dev.service', text)
        self.assertNotIn('sudoers.d/evcowbbe-executor', text)
        self.assertNotIn('evcowbbe-dev.service', text)
        self.assertNotIn('rm -rf -- "$state_dir"', text)


class PortTests(unittest.TestCase):
    def test_fixed_argv_no_shell_and_bounded_ack(self):
        invocations = []
        def runner(argv, **kwargs):
            invocations.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout=b'{"ok":true,"result":true}\n')
        port = VPS2ExecutionPort(runner=runner)
        self.assertTrue(port.release_present(A))
        argv, kw = invocations[0]
        self.assertEqual(argv, ['/usr/bin/sudo', '-n', BRIDGE])
        self.assertNotIn('shell', kw)
        self.assertEqual(json.loads(kw['input']), {'op':'present','sha':A})

    def test_malformed_negative_or_bogus_ack_is_never_success(self):
        for result in (SimpleNamespace(returncode=1, stdout=b''),
                       SimpleNamespace(returncode=0, stdout=b'{}'),
                       SimpleNamespace(returncode=0, stdout=b'{"ok":true,"result":false}')):
            port = VPS2ExecutionPort(runner=lambda _argv, **_kw: result)
            with self.subTest(result=result), self.assertRaises(HostPortError):
                port.build_release(A)

    def test_bridge_rejects_invalid_payload_and_redacts_errors(self):
        class FakeOps:
            def dispatch(self, op, sha):
                raise RuntimeError('secret-credential-leak-marker')
        out, err = io.StringIO(), io.StringIO()
        code = main(stdin=io.BytesIO(b'{"op":"build","sha":"'+A.encode()+b'"}'), stdout=out,
                    stderr=err, ops=FakeOps(), enforce_root=False)
        self.assertEqual(code, 5)
        self.assertNotIn('secret-credential-leak-marker', err.getvalue())
        self.assertFalse(out.getvalue())


class HostGateTests(unittest.TestCase):
    def test_durable_stage_gates_apply_before_privileged_side_effects(self):
        ops = VPS2Operations(runner=lambda *a, **kw: self.fail('no subprocess allowed'))
        with patch('ops.deploy.executor.host.operations.ControlPlane') as mock_control:
            mock_control.return_value.status.return_value = {'runs':[], 'active_controls':[]}
            for stage in ('build','activation','migration','rollback'):
                with self.subTest(stage=stage), self.assertRaises(HostOperationFailed):
                    ops._require_gate(A, stage)


    def test_archive_allows_go_migration_embedding_files_without_sql(self):
        import tarfile
        from ops.deploy.executor.host.operations import VPS2Operations
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as t:
            for filename in ('db/migrations/README.md', 'db/migrations/embed.go'):
                contents = b'not a migration'
                entry = tarfile.TarInfo(filename)
                entry.size = len(contents)
                t.addfile(entry, io.BytesIO(contents))
        ops = VPS2Operations()
        with patch.object(ops, '_github_authorize'), patch.object(ops, '_builder_head'), patch.object(ops, '_runuser', return_value=data.getvalue()):
            self.assertEqual(ops.source(A), [])

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'requires root fixture')
    def test_builder_result_staged_as_root_owned_immutable_pair(self):
        from ops.deploy.executor.host.operations import VPS2Operations, _account_uid
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases, work = root/'releases', root/'build-work'
            releases.mkdir()
            prepared = work/'prepared'/A
            prepared.mkdir(parents=True)
            for name in ('evcowbbe', 'evcowbbe-migrate'):
                (prepared/name).write_bytes(b'fake-binary')
                (prepared/name).chmod(0o750)
            ops = VPS2Operations()
            with patch.object(c, 'RELEASES', releases), patch.object(c, 'BUILD_WORK', work), \
                 patch.object(ops, '_require_gate'), patch.object(ops, '_github_authorize'), \
                 patch.object(ops, '_builder_head'), patch.object(ops, '_runuser'), \
                 patch('ops.deploy.executor.host.operations._account_uid', return_value=os.getuid()):
                self.assertTrue(ops.build(A))
                self.assertTrue(c.verify_release(A))
                self.assertTrue((releases/A/'RELEASE-MANIFEST').is_file())
                self.assertEqual((releases/A).stat().st_uid, 0)
                self.assertTrue(ops.build(A))  # immutable existing pair, no rebuild
                (releases/A/'evcowbbe').write_bytes(b'tampered')
                self.assertFalse(c.verify_release(A))
                with self.assertRaises(HostOperationFailed):
                    ops.build(A)  # never silently replace conflicting release

    def test_root_bridge_cannot_mutate_when_token_missing(self):
        ops = VPS2Operations(runner=lambda *a, **kw: self.fail('no subprocess allowed'))
        with patch.object(c, 'GITHUB_TOKEN', Path('/a/nonexistent/evcow-token-file')):
            with self.assertRaises(HostOperationFailed):
                ops._github_authorize(A)


if __name__ == '__main__':
    unittest.main()
