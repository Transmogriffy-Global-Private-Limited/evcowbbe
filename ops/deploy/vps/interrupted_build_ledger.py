"""Pinned, read-only SQL-ledger evidence for the *first* interrupted VPS2 build.

No retry, SQLite transition or executable entrypoint. The future root-side
coordinator must invoke this while holding the executor's flock, and independently
reverify the host identity immediately before its atomic SQLite transition.

Only the exact verified 2026-10-15 incident is supported. A different run or
baseline MUST receive a separately established and reviewed recovery contract.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ops.deploy.control_plane import canonical_json

RUN_ID = '601f52ab46ed40e4add026a6b50e7d61'
CANDIDATE_SHA = 'ddea3882d1b98d10506a959478e65a608136a01c'
PREVIOUS_SHA = '2ac184c818db49dddf598984b68349d1d33e4c47'
BACKUP_ROOT = Path('/root/evcowbbe-deploy-backups')
BACKUP = BACKUP_ROOT / 'pre-first-promotion-pg-1n5ckFwt' / 'evcowbbe-postgresql.custom'
BACKUP_SHA256 = 'ba94bf6b97e6187790a587dc68ce4f03e58a4715f7b0ca370bf5c46ff7bdc5e1'
EMPTY_LEDGER_SHA256 = '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
MAX_BACKUP_BYTES = 128 * 1024 * 1024


class LedgerRecoveryBlocked(Exception):
    """The independently observed pre-deployment ledger cannot be proved."""


@dataclass(frozen=True)
class PinnedLedgerEvidence:
    run_id: str
    candidate_sha: str
    previous_sha: str
    archive_sha256: str
    ledger_fingerprint: str
    applied_migrations: int
    # Observation is deliberately NOT a write capability or retry authorization.
    result: str = 'read_only_ledger_match_not_authorized_to_reconcile'


def _guarded_backup_file(path: Path) -> None:
    """No symlinks, unexpected owner or writable ancestor in the fixed backup tree."""
    for ancestor in (BACKUP_ROOT, path.parent):
        try:
            info = ancestor.lstat()
        except OSError as exc:
            raise LedgerRecoveryBlocked('backup directory is unavailable') from exc
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise LedgerRecoveryBlocked('backup directory has unsafe ownership or permissions')
    try:
        info = path.lstat()
    except OSError as exc:
        raise LedgerRecoveryBlocked('backup archive is unavailable') from exc
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & 0o022 or not 0 < info.st_size <= MAX_BACKUP_BYTES):
        raise LedgerRecoveryBlocked('backup archive has unsafe type, ownership, permissions or length')


def _verified_archive_sha256() -> str:
    _guarded_backup_file(BACKUP)
    digest = hashlib.sha256()
    try:
        fd = os.open(BACKUP, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
                    or before.st_mode & 0o022 or before.st_size <= 0
                    or before.st_size > MAX_BACKUP_BYTES):
                raise LedgerRecoveryBlocked('backup descriptor is unsafe')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                size = 0
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_BACKUP_BYTES:
                        raise LedgerRecoveryBlocked('backup archive exceeded size bound')
                    digest.update(block)
            after = os.fstat(fd)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                raise LedgerRecoveryBlocked('backup changed during verification')
        finally:
            os.close(fd)
    except OSError as exc:
        raise LedgerRecoveryBlocked('backup archive could not be independently read') from exc
    observed = digest.hexdigest()
    if observed != BACKUP_SHA256:
        raise LedgerRecoveryBlocked('backup SHA-256 does not match pinned evidence')
    return observed


def verify_pinned_first_build_ledger(*, operations: Any = None) -> PinnedLedgerEvidence:
    """Compare trusted live PG observation against a pinned, verified backup.

    No caller may provide a different run, SHA, archive, expected fingerprint,
    or SQL query. `operations` injection is exclusively for disposable tests.
    The trusted coordinator MUST call this inside its existing flock context.
    """
    if os.name != 'posix' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise LedgerRecoveryBlocked('Linux root authority is required')
    archive_digest = _verified_archive_sha256()
    if operations is None:
        from ops.deploy.executor.host.operations import VPS2Operations
        operations = VPS2Operations()
    try:
        snapshot = operations.ledger()
    except Exception as exc:
        raise LedgerRecoveryBlocked('trusted PostgreSQL ledger observation failed') from exc
    if (type(snapshot) is not dict or set(snapshot) != {'ledger_present', 'applied'}
            or snapshot['ledger_present'] is not True
            or type(snapshot['applied']) is not list
            or snapshot['applied'] != []):
        raise LedgerRecoveryBlocked('live migration ledger differs from pinned pre-build state')
    fingerprint = hashlib.sha256(canonical_json(snapshot['applied']).encode('ascii')).hexdigest()
    if fingerprint != EMPTY_LEDGER_SHA256:
        raise LedgerRecoveryBlocked('live ledger fingerprint differs from pinned evidence')
    return PinnedLedgerEvidence(RUN_ID, CANDIDATE_SHA, PREVIOUS_SHA,
                                archive_digest, fingerprint, 0)
