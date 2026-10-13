"""Read-only VPS2 inspection, never a retry or mutation coordinator."""
from __future__ import annotations

import json
import os
import sys

from ops.deploy.control_plane import ControlPlane
from ops.deploy.executor.host.operations import VPS2Operations
from ops.deploy.executor.host import contract as c


def inspect() -> dict:
    if os.geteuid() != 0:
        raise PermissionError('read-only root observer requires root')
    cp = ControlPlane(c.STATE_DB)
    snapshot = cp.status(verbose=True)
    operations = VPS2Operations()
    current = operations.active()
    verified = operations.verify(current)
    ledger = operations.ledger()
    return {
        'schema_version': snapshot['schema_version'],
        'accepted_requests': len(snapshot['requests']),
        'runs': [
            {'id': r['id'], 'sha': r['sha'], 'status': r['status']}
            for r in snapshot['runs']
        ],
        'active_controls': snapshot['active_controls'],
        'active_release': current,
        'running_release_verified': verified,
        'ledger_present': ledger['ledger_present'],
        'applied_migrations': len(ledger['applied']),
        'result': 'observation_only',
    }


def main() -> int:
    if len(sys.argv) != 1:
        print('{"error":"no_arguments_permitted"}', file=sys.stderr)
        return 2
    try:
        print(json.dumps(inspect(), sort_keys=True, separators=(',', ':')))
        return 0
    except Exception:
        print('{"error":"recovery_observation_unavailable"}', file=sys.stderr)
        return 5


if __name__ == '__main__':
    raise SystemExit(main())
