"""Passive, systemd-invoked recovery hint consumer. It never deploys."""

from __future__ import annotations

import sys
import sqlite3
from typing import TextIO

from ops.deploy.control_plane import ControlPlane, ControlPlaneError, canonical_json
from ops.deploy.vps.common import STATE_DB_PATH


def inspect_pending(state_db: str = STATE_DB_PATH) -> dict[str, object]:
    control = ControlPlane(state_db)
    # Only historical admissions are available without verified remote HEAD and
    # observed runtime. Never call the number of admissions "pending work".
    def count(connection: sqlite3.Connection) -> int:
        return int(connection.execute("SELECT COUNT(*) FROM deployment_requests").fetchone()[0])
    total = control._read(count)
    return {"mode": "passive", "accepted_requests_total": total,
            "reconciliation_state": "not_evaluated", "deployed": False}


def main(stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        stdout.write(canonical_json(inspect_pending()) + "\n")
        stdout.flush()
        return 0
    except ControlPlaneError as exc:
        stderr.write(canonical_json({"error": exc.__class__.__name__}) + "\n")
        stderr.flush()
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
