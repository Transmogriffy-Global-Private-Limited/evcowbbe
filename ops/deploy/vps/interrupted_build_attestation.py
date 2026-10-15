"""Incident-scoped, root-owned durable *evidence* artifact, NOT recovery authority.

Only a future separately reviewed coordinator can call the public entrypoint
under the executor flock and a continuously held PostgreSQL writer fence.
Writing this file NEVER closes SQLite, releases a lock, arms the executor, or
permits retry. An orphan manifest after a crash is historical evidence, not a
successful reconciliation.

The fixed destination must be provisioned beforehand by a root-reviewed
installation step; this module does not create or relax directories.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from ops.deploy.control_plane import canonical_json
from ops.deploy.vps.interrupted_build_coordinator import RecoveryEvidencePreflight
from ops.deploy.vps import interrupted_build_ledger as incident

ROOT = Path('/opt/evcowbbe-deploy')
EVIDENCE_DIRECTORY = ROOT / 'recovery-attestations'
FORMAT = 'evcowbbe-interrupted-build-evidence-v1'
MAX_JSON_BYTES = 16 * 1024


class EvidencePersistenceBlocked(Exception):
    """Manifest was not positively persisted and revalidated; stop recovery."""


@dataclass(frozen=True)
class DurableEvidenceManifest:
    run_id: str
    evidence_sha256: str
    path: str
    created: bool
    result: str = 'durable_evidence_only_not_authorized_to_reconcile'


def _canonical_preflight(preflight: RecoveryEvidencePreflight) -> bytes:
    if type(preflight) is not RecoveryEvidencePreflight or (
        preflight.run_id != incident.RUN_ID
        or preflight.candidate_sha != incident.CANDIDATE_SHA
        or preflight.previous_sha != incident.PREVIOUS_SHA
        or preflight.event_sequences != (4, 5, 6)
        or preflight.result != 'read_only_preflight_not_authorized_to_transition'
    ):
        raise EvidencePersistenceBlocked('untrusted or mismatched incident preflight')
    try:
        import json
        document = json.loads(preflight.evidence_json)
        actual = canonical_json(document)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EvidencePersistenceBlocked('invalid evidence JSON') from exc
    if type(document) is not dict or set(document) != {
        'format', 'run_id', 'candidate_sha', 'previous_sha', 'event_sequences',
        'archive_sha256', 'prebuild_ledger_sha256', 'applied_migrations',
        'host_observation', 'ledger_observation', 'readonly_rechecks',
    } or document != {
        'format': FORMAT,
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
    }:
        raise EvidencePersistenceBlocked('unexpected attestation fields or incident identity')
    if actual != preflight.evidence_json:
        raise EvidencePersistenceBlocked('evidence JSON is not canonical')
    raw = actual.encode('ascii')
    if not 0 < len(raw) <= MAX_JSON_BYTES:
        raise EvidencePersistenceBlocked('evidence JSON exceeds size bound')
    digest = hashlib.sha256(raw).hexdigest()
    if preflight.evidence_sha256 != digest:
        raise EvidencePersistenceBlocked('preflight SHA-256 is not the evidence digest')
    return raw


def _guarded_dir(path: Path, *, private: bool) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        raise EvidencePersistenceBlocked('root-owned evidence directory is not provisioned') from exc
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0
            or (stat.S_IMODE(st.st_mode) != 0o700 if private else bool(st.st_mode & 0o022))):
        raise EvidencePersistenceBlocked('unsafe evidence directory ownership or mode')


def _read_exact(dir_fd: int, name: str, expected: bytes) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError as exc:
        raise EvidencePersistenceBlocked('manifest path unavailable or symlinked') from exc
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0
                or stat.S_IMODE(st.st_mode) != 0o400
                or st.st_nlink != 1 or st.st_size != len(expected)):
            raise EvidencePersistenceBlocked('existing manifest has unsafe metadata')
        actual = os.read(fd, MAX_JSON_BYTES + 1)
        if actual != expected or os.read(fd, 1):
            raise EvidencePersistenceBlocked('existing manifest differs from verified evidence')
    finally:
        os.close(fd)


def _persist(preflight: RecoveryEvidencePreflight, directory: Path) -> DurableEvidenceManifest:
    """Private dependency-injection seam for disposable filesystem tests only."""
    if os.name != 'posix' or os.geteuid() != 0:
        raise EvidencePersistenceBlocked('Linux root identity required')
    raw = _canonical_preflight(preflight)
    digest = hashlib.sha256(raw).hexdigest()
    _guarded_dir(directory.parent, private=False)
    _guarded_dir(directory, private=True)
    filename = f'{incident.RUN_ID}.{digest}.json'
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise EvidencePersistenceBlocked('unable to open guarded evidence directory') from exc
    try:
        inode = os.fstat(dir_fd)
        pathname_inode = directory.lstat()
        if (not stat.S_ISDIR(inode.st_mode) or inode.st_uid != 0
                or stat.S_IMODE(inode.st_mode) != 0o700
                or (inode.st_dev, inode.st_ino) != (pathname_inode.st_dev, pathname_inode.st_ino)):
            raise EvidencePersistenceBlocked('evidence directory changed during open')
        try:
            _read_exact(dir_fd, filename, raw)
            os.fsync(dir_fd)
            return DurableEvidenceManifest(incident.RUN_ID, digest, str(directory / filename), False)
        except EvidencePersistenceBlocked as exc:
            # Only an absent path permits creating evidence. A symlink or
            # conflicting existing path must never be overwritten/replaced.
            try:
                os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise exc

        temp = f'.{incident.RUN_ID}.{secrets.token_hex(16)}.tmp'
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
        except OSError as exc:
            raise EvidencePersistenceBlocked('cannot allocate private evidence temp file') from exc
        linked = False
        try:
            try:
                with os.fdopen(fd, 'wb', closefd=False) as output:
                    output.write(raw)
                    output.flush()
                os.fchmod(fd, 0o400)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(temp, filename, src_dir_fd=dir_fd,
                        dst_dir_fd=dir_fd, follow_symlinks=False)
                linked = True
            except FileExistsError:
                # A concurrent actor created the exact name: only identical
                # persisted bytes and safe metadata allow idempotent return.
                _read_exact(dir_fd, filename, raw)
            except OSError as exc:
                raise EvidencePersistenceBlocked('cannot publish immutable evidence') from exc
        finally:
            os.unlink(temp, dir_fd=dir_fd)
        os.fsync(dir_fd)
        _read_exact(dir_fd, filename, raw)
        return DurableEvidenceManifest(incident.RUN_ID, digest, str(directory / filename), linked)
    except (OSError, ValueError) as exc:
        raise EvidencePersistenceBlocked('durable evidence persistence failed') from exc
    finally:
        os.close(dir_fd)


def persist_first_build_evidence(preflight: RecoveryEvidencePreflight) -> DurableEvidenceManifest:
    """Fixed incident only. Does not authorize the SQLite transition or retry."""
    return _persist(preflight, EVIDENCE_DIRECTORY)
