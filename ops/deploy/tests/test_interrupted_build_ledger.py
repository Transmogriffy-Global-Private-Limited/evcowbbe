"""No VPS, PostgreSQL or deployment changes: mocks and disposable files only."""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ops.deploy.vps import interrupted_build_ledger as ledger

ACTUAL_BACKUP_GUARD = ledger._guarded_backup_file


class LedgerRecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.archive = folder / 'pg.custom'
        self.archive.write_bytes(b'isolated test fixture only')
        # The isolated CI runner is unprivileged. Preserve actual inode,
        # size, mode and timestamps while simulating only the root UID that
        # the real VPS archive must have. Digest checks still read the bytes.
        real_fstat = os.fstat
        identity = self.archive.stat()

        def root_archive_fstat(fd):
            info = real_fstat(fd)
            if (info.st_dev, info.st_ino) != (identity.st_dev, identity.st_ino):
                return info
            return SimpleNamespace(
                st_mode=info.st_mode, st_uid=0, st_size=info.st_size,
                st_dev=info.st_dev, st_ino=info.st_ino,
                st_mtime_ns=info.st_mtime_ns,
            )

        self.patches = [
            patch.object(ledger.os, 'fstat', side_effect=root_archive_fstat),
            patch.object(ledger, 'BACKUP', self.archive),
            patch.object(ledger, 'BACKUP_SHA256', hashlib.sha256(self.archive.read_bytes()).hexdigest()),
            patch.object(ledger, '_guarded_backup_file'),
        ]
        for guard in self.patches:
            guard.start()
            self.addCleanup(guard.stop)

    def test_fingerprint_contract_matches_existing_ledger_hash(self):
        self.assertEqual(hashlib.sha256(b'[]').hexdigest(), ledger.EMPTY_LEDGER_SHA256)

    @unittest.skipUnless(os.name == 'posix', 'host-specific file descriptor and UID checks')
    def test_matching_snapshot_returns_read_only_evidence(self):
        class Good:
            def ledger(self):
                return {'ledger_present': True, 'applied': []}
        with patch.object(ledger.os, 'geteuid', return_value=0):
            value = ledger.verify_pinned_first_build_ledger(operations=Good())
        self.assertEqual((value.run_id, value.candidate_sha, value.previous_sha),
                         (ledger.RUN_ID, ledger.CANDIDATE_SHA, ledger.PREVIOUS_SHA))
        self.assertEqual(value.applied_migrations, 0)
        self.assertEqual(value.result, 'read_only_ledger_match_not_authorized_to_reconcile')

    @unittest.skipUnless(os.name == 'posix', 'host-specific file descriptor and UID checks')
    def test_missing_table_or_nonempty_or_malformed_ledger_blocks(self):
        class Observer:
            def __init__(self, value): self.value = value
            def ledger(self): return self.value
        examples = [
            {'ledger_present': False, 'applied': []},
            {'ledger_present': True, 'applied': [{'version': 1}]},
            {'ledger_present': True, 'applied': None},
            {'ledger_present': True, 'applied': [], 'extra': True},
            {'ledger_present': 1, 'applied': []},
        ]
        with patch.object(ledger.os, 'geteuid', return_value=0):
            for example in examples:
                with self.subTest(example=example), self.assertRaises(ledger.LedgerRecoveryBlocked):
                    ledger.verify_pinned_first_build_ledger(operations=Observer(example))

    @unittest.skipUnless(os.name == 'posix', 'host-specific file descriptor and UID checks')
    def test_bad_archive_hash_blocks_before_observer(self):
        class Unused:
            def ledger(self): raise AssertionError('should not observe live database')
        self.archive.write_bytes(b'TAMPERED')
        with patch.object(ledger.os, 'geteuid', return_value=0), self.assertRaises(ledger.LedgerRecoveryBlocked):
            ledger.verify_pinned_first_build_ledger(operations=Unused())

    @unittest.skipUnless(os.name == 'posix', 'host-specific file descriptor and UID checks')
    def test_missing_archive_blocks_before_observer(self):
        self.archive.unlink()
        with patch.object(ledger.os, 'geteuid', return_value=0), self.assertRaises(ledger.LedgerRecoveryBlocked):
            ledger.verify_pinned_first_build_ledger(operations=None)

    @unittest.skipUnless(os.name == 'posix', 'host-specific file permissions')
    def test_real_archive_guards_refuse_dangling_symlinks_and_weak_permissions(self):
        # This test exercises the actual path/type/mode checks rather than the
        # mock used for identity-comparison tests above.
        original = ACTUAL_BACKUP_GUARD
        folder = Path(self.tmp.name)
        backup_root = folder / 'backups'
        backup_root.mkdir(mode=0o700)
        holding = backup_root / 'point-in-time'
        holding.mkdir(mode=0o700)
        archive = holding / 'backup.custom'
        archive.write_bytes(b'verified fixture')
        # Test actual type and mode checks under simulated root ownership.
        # Do not weaken the production function for unprivileged CI.
        real_lstat = Path.lstat

        def root_archive_lstat(path):
            info = real_lstat(path)
            if path in (backup_root, holding, archive):
                return SimpleNamespace(
                    st_mode=info.st_mode, st_uid=0, st_size=info.st_size,
                )
            return info

        with patch.object(ledger, 'BACKUP_ROOT', backup_root), \
             patch.object(Path, 'lstat', root_archive_lstat):
            original(archive)
            archive.chmod(0o666)
            with self.assertRaises(ledger.LedgerRecoveryBlocked):
                original(archive)
            archive.chmod(0o600)
            archive.unlink()
            archive.symlink_to(holding / 'missing')
            with self.assertRaises(ledger.LedgerRecoveryBlocked):
                original(archive)
            archive.unlink()
            archive.write_bytes(b'safe')
            holding.chmod(0o777)
            with self.assertRaises(ledger.LedgerRecoveryBlocked):
                original(archive)

    @unittest.skipUnless(os.name == 'posix', 'host-specific observation')
    def test_ledger_exception_fails_closed(self):
        class Broken:
            def ledger(self):
                raise TimeoutError('simulated observer timeout')
        with patch.object(ledger.os, 'geteuid', return_value=0):
            with self.assertRaises(ledger.LedgerRecoveryBlocked):
                ledger.verify_pinned_first_build_ledger(operations=Broken())

    def test_root_required_fail_closed(self):
        with patch.object(ledger.os, 'geteuid', return_value=1000, create=True), self.assertRaises(ledger.LedgerRecoveryBlocked):
            ledger.verify_pinned_first_build_ledger()


if __name__ == '__main__':
    unittest.main()
