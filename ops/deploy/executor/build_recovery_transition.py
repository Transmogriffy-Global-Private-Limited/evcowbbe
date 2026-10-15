"""Internal SQLite half of an interrupted-build reconciliation, not an entrypoint.

Only a separately privileged coordinator holding the root-owned executor flock
and completing independent host + PostgreSQL identity attestation may call this.
The digest is an audit reference, NOT proof or an authorization token. This
module contains no OS observation, subprocess, CLI, retry, or root authority.
"""
from __future__ import annotations

import json
from typing import Any

from ops.deploy.control_plane import (
    ControlPlane, ExecutionBlocked, ValidationError,
)


RECOVERY_KIND = 'interrupted_build_recovery_attested'
RECOVERY_OUTCOME = 'interrupted_build_no_external_release_verified'


def _matches_attestation(raw: str, expected: dict[str, Any]) -> bool:
    try:
        return json.loads(raw) == expected
    except (TypeError, ValueError):
        return False


def _matches_transition(raw: str, outcome: str) -> bool:
    try:
        event = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return (isinstance(event, dict) and event.get('from') == 'running'
            and event.get('to') == 'failed' and event.get('outcome') == outcome)


def finalize_attested_build_interruption(
    control: ControlPlane, *, run_id: str, candidate_sha: str,
    previous_sha: str, expected_event_sequences: tuple[int, int, int],
    evidence_sha256: str, operator: str,
) -> dict[str, Any]:
    """Atomically verify database state and close ONLY an attested build intent.

    This has no standalone authorization to mutate deployment state. The caller
    MUST be the verified root-side coordinator under a continuous kernel lock.
    `evidence_sha256` identifies independently attested evidence; supplying a
    well-formed digest does not independently establish that evidence exists.
    No entrypoint or external request should expose this primitive directly.
    """
    run_id = control._validate_identifier('run_id', run_id, 64)
    candidate_sha = control._validate_sha(candidate_sha)
    previous_sha = control._validate_sha(previous_sha)
    evidence_sha256 = control._validate_plan(evidence_sha256)
    operator = control._validate_identifier('operator', operator, 128)
    if previous_sha == candidate_sha:
        raise ValidationError('previous and candidate SHA cannot match')
    if (type(expected_event_sequences) is not tuple
            or len(expected_event_sequences) != 3
            or any(type(x) is not int or x < 1 for x in expected_event_sequences)
            or tuple(sorted(set(expected_event_sequences))) != expected_event_sequences):
        raise ValidationError('three exact increasing event sequences are required')

    attestation = {
        'candidate_sha': candidate_sha,
        'previous_sha': previous_sha,
        'evidence_sha256': evidence_sha256,
        'operator': operator,
        'event_sequences': list(expected_event_sequences),
    }

    def operation(conn):
        runs = conn.execute('SELECT * FROM deployment_runs').fetchall()
        if len(runs) != 1 or runs[0]['id'] != run_id or runs[0]['sha'] != candidate_sha:
            raise ExecutionBlocked('expected unique deployment run differs')
        run = runs[0]
        req = conn.execute('SELECT * FROM deployment_requests WHERE id = ?',
                           (run['request_id'],)).fetchone()
        if (req is None or req['sha'] != candidate_sha
                or req['status'] != 'accepted' or req['branch'] != 'prod'):
            raise ExecutionBlocked('original source request differs')
        if conn.execute("SELECT 1 FROM migration_decisions LIMIT 1").fetchone():
            raise ExecutionBlocked('migration decisions present')
        if conn.execute("SELECT 1 FROM operator_controls WHERE status = 'active' LIMIT 1").fetchone():
            raise ExecutionBlocked('active operator controls present')
        rows = conn.execute(
            "SELECT sequence, kind, entity_type, entity_id, details FROM deployment_events "
            "WHERE entity_type = 'deployment_run' ORDER BY sequence"
        ).fetchall()
        if any(x['entity_id'] != run_id for x in rows):
            raise ExecutionBlocked('run event belongs to another identity')
        if run['status'] == 'failed':
            # Idempotent outcome lookup: never append another audit transition.
            if (run['outcome'] != RECOVERY_OUTCOME or len(rows) != 5
                    or [x['kind'] for x in rows] != [
                        'run_created', 'run_execution_claimed', 'executor_checkpoint',
                        RECOVERY_KIND, 'run_transitioned',
                    ] or tuple(x['sequence'] for x in rows[:3]) != expected_event_sequences
                    or not _matches_attestation(rows[3]['details'], attestation)
                    or not _matches_transition(rows[4]['details'], RECOVERY_OUTCOME)):
                raise ExecutionBlocked('terminal run has no matching recovery attestation')
            return {'created': False, 'run_id': run_id, 'status': 'failed'}
        if (run['status'] != 'running' or run['attempt'] != 1
                or not run['execution_claimed_at'] or run['outcome'] is not None
                or run['error_details'] is not None or run['terminal_at'] is not None
                or run['migration_decision_id'] is not None):
            raise ExecutionBlocked('run is not exactly an unresolved first execution')
        if len(rows) != 3 or tuple(x['sequence'] for x in rows) != expected_event_sequences:
            raise ExecutionBlocked('run event count or sequence changed')
        if [x['kind'] for x in rows] != [
            'run_created', 'run_execution_claimed', 'executor_checkpoint',
        ] or any(x['entity_id'] != run_id for x in rows):
            raise ExecutionBlocked('run event provenance changed')
        try:
            checkpoint = json.loads(rows[2]['details'])
        except (ValueError, TypeError):
            raise ExecutionBlocked('checkpoint details are invalid') from None
        if checkpoint != {
            'stage': 'build', 'phase': 'intent', 'sha': candidate_sha,
            'executor_identity': 'evcow-orchestrator',
        }:
            raise ExecutionBlocked('outstanding intent is not the expected build')
        control._append_event(conn, RECOVERY_KIND, 'deployment_run', run_id, attestation)
        control._finish_run(
            conn, run, 'failed', RECOVERY_OUTCOME,
            {'code': 'BUILD_INTERRUPTED_RECONCILED', 'evidence_sha256': evidence_sha256},
        )
        return {'created': True, 'run_id': run_id, 'status': 'failed'}

    return control._transaction(operation)
