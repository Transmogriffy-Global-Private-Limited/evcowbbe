"""Source-only, read-only K3B-4 recovery evidence coordinator.

This module has NO operation that finalizes a deployment run, writes an
attestation manifest, issues an operator authorization, or arms an executor.
The result is a preflight snapshot, NOT a commit permit: PostgreSQL's observer
releases its advisory lock after each read. Before any recovery mutation a
separate cross-system writer fence and durable evidence protocol are required.

`_prepare` parameters are exclusively for disposable tests. Only the zero-
argument public function may be used for production observations.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ops.deploy.control_plane import canonical_json
from ops.deploy.executor.build_recovery import (
    REQUIRED_HOST_FACTS, RecoveryBlocked, assess_interrupted_build,
)
from ops.deploy.vps import interrupted_build_host as host
from ops.deploy.vps import interrupted_build_ledger as ledger

EXPECTED_EVENT_SEQUENCES = (4, 5, 6)  # Read-only VPS2 audit verified on 2026-10-15.
DB = Path('/var/lib/evcowbbe-deploy/orchestrator.sqlite3')


class CoordinatorBlocked(Exception):
    """Fail closed: do not interpret this preflight as recovery permission."""


@dataclass(frozen=True)
class RecoveryEvidencePreflight:
    run_id: str
    candidate_sha: str
    previous_sha: str
    event_sequences: tuple[int, int, int]
    evidence_sha256: str
    evidence_json: str
    result: str = 'read_only_preflight_not_authorized_to_transition'


def _snapshot_from_sqlite(database: Path) -> dict:
    """Read exact rows through SQLite's *read-only* URI; never initialize DB."""
    if database.is_symlink() or not database.is_file():
        raise CoordinatorBlocked('SQLite journal path unavailable or symbolic link')
    # No SQLite initialize(), PRAGMA journal_mode write or db creation.
    try:
        conn = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)
    except (ValueError, sqlite3.Error) as exc:
        raise CoordinatorBlocked('SQLite readonly journal access unavailable') from exc
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only = ON')
        conn.execute('BEGIN')
        runs = [dict(x) for x in conn.execute(
            'SELECT id, request_id, sha, status, attempt, outcome, '
            'execution_claimed_at FROM deployment_runs ORDER BY id')]
        controls = [dict(x) for x in conn.execute(
            "SELECT id FROM operator_controls WHERE status='active' ORDER BY id")]
        decisions = [dict(x) for x in conn.execute(
            'SELECT id FROM migration_decisions ORDER BY id')]
        requests = [dict(x) for x in conn.execute(
            'SELECT id,sha,branch,status FROM deployment_requests ORDER BY id')]
        events = []
        for row in conn.execute(
            'SELECT sequence,kind,entity_type,entity_id,details FROM deployment_events '
            'ORDER BY sequence'):
            parsed = json.loads(row['details'])
            if type(parsed) is not dict:
                raise CoordinatorBlocked('non-object audit event details')
            events.append({**dict(row), 'details': parsed})
        conn.execute('ROLLBACK')
        return {
            'runs': runs, 'controls': controls, 'decisions': decisions,
            'requests': requests, 'events': events,
        }
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise CoordinatorBlocked('SQLite snapshot unavailable or malformed') from exc
    finally:
        conn.close()


def _verify_live_host_again() -> None:
    """Reobserve while the outer context still owns the immutable flock."""
    from ops.deploy.executor.host.operations import VPS2Operations
    host._quiescent(host._systemctl)
    host._no_builder_processes(ledger.CANDIDATE_SHA)
    host._no_artifacts(ledger.CANDIDATE_SHA, host._builder_uid())
    operation = VPS2Operations()
    if (operation.active() != ledger.PREVIOUS_SHA
            or operation.verify(ledger.PREVIOUS_SHA) is not True):
        raise CoordinatorBlocked('previous application runtime changed during preflight')
    host._no_builder_processes(ledger.CANDIDATE_SHA)
    host._no_artifacts(ledger.CANDIDATE_SHA, host._builder_uid())
    host._quiescent(host._systemctl)


def _prepare(*, database: Path, observer_context: Callable,
             ledger_prover: Callable, host_recheck: Callable[[], None]) -> RecoveryEvidencePreflight:
    """Internal test seam, NOT a callable privileged recovery API."""
    if os.name != 'posix' or os.geteuid() != 0:
        raise CoordinatorBlocked('Linux root identity required')
    with observer_context(ledger.CANDIDATE_SHA, ledger.PREVIOUS_SHA) as observed:
        if (type(observed) is not host.FencedHostObservation
                or observed.candidate_sha != ledger.CANDIDATE_SHA
                or observed.previous_sha != ledger.PREVIOUS_SHA
                or observed.result != 'host_quiescence_only_not_authorized_to_reconcile'):
            raise CoordinatorBlocked('host observation identity or contract mismatch')
        first_ledger = ledger_prover()
        if (type(first_ledger) is not ledger.PinnedLedgerEvidence
                or first_ledger.run_id != ledger.RUN_ID
                or first_ledger.candidate_sha != ledger.CANDIDATE_SHA
                or first_ledger.previous_sha != ledger.PREVIOUS_SHA
                or first_ledger.archive_sha256 != ledger.BACKUP_SHA256
                or first_ledger.ledger_fingerprint != ledger.EMPTY_LEDGER_SHA256
                or first_ledger.applied_migrations != 0
                or first_ledger.result != 'read_only_ledger_match_not_authorized_to_reconcile'):
            raise CoordinatorBlocked('PostgreSQL observation identity mismatch')
        snapshot = _snapshot_from_sqlite(database)
        if (len(snapshot['requests']) != 3 or len(snapshot['runs']) != 1):
            raise CoordinatorBlocked('unexpected historic requests or deployment runs')
        run = snapshot['runs'][0]
        matching_request = [x for x in snapshot['requests'] if x['id'] == run['request_id']]
        if (len(matching_request) != 1 or matching_request[0]['sha'] != ledger.CANDIDATE_SHA
                or matching_request[0]['branch'] != 'prod'
                or matching_request[0]['status'] != 'accepted'):
            raise CoordinatorBlocked('run request provenance mismatch')
        run_events = [x for x in snapshot['events'] if x['entity_type'] == 'deployment_run']
        if (len(run_events) != 3 or
                tuple(x['sequence'] for x in run_events) != EXPECTED_EVENT_SEQUENCES):
            raise CoordinatorBlocked('unexpected run-specific event sequence')
        # No host_facts parameter exposed: positives are established solely
        # by successfully returning from the trusted observers above.
        assess_interrupted_build(
            run_id=ledger.RUN_ID, candidate_sha=ledger.CANDIDATE_SHA,
            previous_sha=ledger.PREVIOUS_SHA,
            runs=snapshot['runs'], events=run_events,
            active_controls=snapshot['controls'],
            migration_decisions=snapshot['decisions'],
            host_facts={key: True for key in REQUIRED_HOST_FACTS},
        )
        host_recheck()
        second_ledger = ledger_prover()
        if second_ledger != first_ledger:
            raise CoordinatorBlocked('ledger changed between fenced observations')
        # The body is nonsecret metadata only; the K3B-5 evidence-storage
        # protocol must persist this exact canonical JSON before any commit.
        record = {
            'format': 'evcowbbe-interrupted-build-evidence-v1',
            'run_id': ledger.RUN_ID,
            'candidate_sha': ledger.CANDIDATE_SHA,
            'previous_sha': ledger.PREVIOUS_SHA,
            'event_sequences': list(EXPECTED_EVENT_SEQUENCES),
            'archive_sha256': first_ledger.archive_sha256,
            'prebuild_ledger_sha256': first_ledger.ledger_fingerprint,
            'applied_migrations': first_ledger.applied_migrations,
            'host_observation': observed.result,
            'ledger_observation': first_ledger.result,
            'readonly_rechecks': 2,
        }
        canonical = canonical_json(record)
        digest = hashlib.sha256(canonical.encode('ascii')).hexdigest()
        return RecoveryEvidencePreflight(
            ledger.RUN_ID, ledger.CANDIDATE_SHA, ledger.PREVIOUS_SHA,
            EXPECTED_EVENT_SEQUENCES, digest, canonical,
        )


def prepare_first_build_recovery() -> RecoveryEvidencePreflight:
    """Only production API: root-only, no caller paths, facts, or overrides."""
    return _prepare(
        database=DB, observer_context=host.fenced_build_observation,
        ledger_prover=ledger.verify_pinned_first_build_ledger,
        host_recheck=_verify_live_host_again,
    )
