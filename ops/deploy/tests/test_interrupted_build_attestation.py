"""Durable, root-owned evidence persistence tests: no production filesystem writes."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ops.deploy.control_plane import canonical_json
from ops.deploy.vps import interrupted_build_attestation as att
from ops.deploy.vps import interrupted_build_ledger as incident
from ops.deploy.vps.interrupted_build_coordinator import RecoveryEvidencePreflight


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0,
                     'root-only POSIX filesystem semantics')
class DurableEvidenceManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'evidence'
        os.chmod(self.tmp.name, 0o700)
        self.directory.mkdir(mode=0o700)
        self.preflight = self._valid_preflight()

    @staticmethod
    def _valid_preflight():
        document = {
            'format': att.FORMAT,
            'run_id': incident.RUN_ID,
            'candidate_sha': incident.CANDIDATE_SHA,
            'previous_sha': incident.PREVIOUS_SHA,
            'event_sequences': [4, 5, 6],
            'archive_sha256': incident.BACKUP_SHA256,
            'prebuild_ledger_sha256': incident.EMPTY_LEDGER_SHA256,
            'applied_migrations': 0,
            'host_observation': 'host_quiescence_only_not_authorized_to_reconcile',
            'ledger_observation': 'read_only_ledger_match_not_authorized_to_reconcile',
            'readonly_rechecks': 2,
        }
        value = canonical_json(document)
        return RecoveryEvidencePreflight(
            incident.RUN_ID, incident.CANDIDATE_SHA, incident.PREVIOUS_SHA,
            (4, 5, 6), hashlib.sha256(value.encode('ascii')).hexdigest(), value,
        )

    def test_creation_is_immutable_and_auditable(self):
        result = att._persist(self.preflight, self.directory)
        self.assertTrue(result.created)
        self.assertEqual(result.result, 'durable_evidence_only_not_authorized_to_reconcile')
        self.assertEqual(result.evidence_sha256, self.preflight.evidence_sha256)
        output = Path(result.path)
        self.assertEqual(output.read_bytes(), self.preflight.evidence_json.encode('ascii'))
        self.assertEqual(output.stat().st_mode & 0o7777, 0o400)
        self.assertEqual(output.stat().st_nlink, 1)
        self.assertEqual(output.stat().st_uid, 0)
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_duplicate_is_idempotent_and_does_not_rewrite(self):
        first = att._persist(self.preflight, self.directory)
        old = Path(first.path).stat().st_ino
        second = att._persist(self.preflight, self.directory)
        self.assertFalse(second.created)
        self.assertEqual(first.path, second.path)
        self.assertEqual(old, Path(second.path).stat().st_ino)

    def test_evidence_digest_mismatch_refuses_without_writing(self):
        invalid = replace(self.preflight, evidence_sha256='0'*64)
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(invalid, self.directory)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_noncanonical_json_refuses(self):
        other = replace(self.preflight, evidence_json=json.dumps(json.loads(self.preflight.evidence_json)))
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(other, self.directory)

    def test_changed_incident_identity_refuses(self):
        invalid = replace(self.preflight, previous_sha='0'*40)
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(invalid, self.directory)

    def test_existing_corrupted_bytes_refuses(self):
        result = att._persist(self.preflight, self.directory)
        output = Path(result.path)
        output.chmod(0o600)
        output.write_text('corrupted', encoding='ascii')
        output.chmod(0o400)
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(self.preflight, self.directory)
        self.assertEqual(output.read_text(), 'corrupted')

    def test_existing_symlink_refuses(self):
        filename = f'{incident.RUN_ID}.{self.preflight.evidence_sha256}.json'
        (self.directory / filename).symlink_to(self.directory / 'missing')
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(self.preflight, self.directory)
        self.assertTrue((self.directory / filename).is_symlink())

    def test_existing_hardlink_refuses(self):
        result = att._persist(self.preflight, self.directory)
        os.link(result.path, self.directory / 'unexpected-hardlink')
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(self.preflight, self.directory)

    def test_unsafe_directory_permissions_refuse(self):
        self.directory.chmod(0o755)
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(self.preflight, self.directory)

    def test_missing_directory_refuses_without_creating_it(self):
        missing = self.directory / 'missing'
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(self.preflight, missing)
        self.assertFalse(missing.exists())

    def test_symlink_directory_refuses(self):
        other = Path(self.tmp.name) / 'symlink'
        other.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(att.EvidencePersistenceBlocked):
            att._persist(self.preflight, other)

    def test_simulated_disk_sync_failure_blocks_acknowledgement(self):
        actual = att.os.fsync
        calls = 0
        def faulty(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated durable directory sync failure')
            return actual(fd)
        with patch.object(att.os, 'fsync', side_effect=faulty):
            with self.assertRaises(att.EvidencePersistenceBlocked):
                att._persist(self.preflight, self.directory)
        # Crash after link may leave orphan evidence. No success was returned.
        self.assertEqual(len(list(self.directory.iterdir())), 1)
        restored = att._persist(self.preflight, self.directory)
        self.assertFalse(restored.created)

    def test_process_must_be_root(self):
        with patch.object(att.os, 'geteuid', return_value=1000):
            with self.assertRaises(att.EvidencePersistenceBlocked):
                att._persist(self.preflight, self.directory)

if __name__ == '__main__':
    unittest.main()
