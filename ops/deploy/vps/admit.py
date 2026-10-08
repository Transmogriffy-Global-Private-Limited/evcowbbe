"""The sole SQLite-writing ingress operation, run as evcow-orchestrator."""

from __future__ import annotations

import sys
from typing import TextIO

from ops.deploy.control_plane import ControlPlane, ControlPlaneError
from ops.deploy.vps.common import MAX_REQUEST_BYTES, STATE_DB_PATH, encode_json, parse_exact_request


def admit(raw: bytes, state_db: str = STATE_DB_PATH) -> bytes:
    payload = parse_exact_request(raw, state_db)
    result = ControlPlane(state_db).submit_request(payload)
    request = result["request"]
    # submit_request returns only after its SQLite transaction has committed.
    return encode_json(
        {
            "accepted": True,
            "created": result["created"],
            "request_id": request["id"],
            "sha": request["sha"],
        }
    )


def main(stdin: object = None, stdout: TextIO | None = None, stderr: TextIO | None = None, *, state_db: str = STATE_DB_PATH) -> int:
    input_stream = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        stdout.buffer.write(admit(input_stream.read(MAX_REQUEST_BYTES + 1), state_db))
        stdout.flush()
        return 0
    except ControlPlaneError as exc:
        stderr.write(encode_json({"accepted": False, "error": exc.__class__.__name__}).decode("ascii"))
        stderr.flush()
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
