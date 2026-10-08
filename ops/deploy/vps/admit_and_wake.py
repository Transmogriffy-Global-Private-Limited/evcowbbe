"""Root-owned fixed bridge: orchestrator admission, then nonauthoritative wake."""

from __future__ import annotations

import subprocess
import sys
from typing import TextIO

from ops.deploy.control_plane import ValidationError
from ops.deploy.vps.common import MAX_REQUEST_BYTES, decode_ack, encode_json


ORCHESTRATOR_ADMIT = "/opt/evcowbbe-deploy/current/bin/evcowbbe-deploy-admit"
RECONCILE_UNIT = "evcowbbe-deploy-reconcile.service"


def run(raw: bytes, runner=subprocess.run) -> tuple[int, bytes]:
    admitted = runner(
        ["/usr/sbin/runuser", "-u", "evcow-orchestrator", "--", ORCHESTRATOR_ADMIT],
        input=raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if admitted.returncode != 0:
        return admitted.returncode or 5, encode_json({"accepted": False, "error": "admission_failed"})
    decode_ack(admitted.stdout)
    wake = runner(
        ["/usr/bin/systemctl", "start", RECONCILE_UNIT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if wake.returncode != 0:
        return 5, encode_json({"accepted": False, "admitted": True, "error": "wake_failed"})
    return 0, admitted.stdout


def main(stdin: object = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    input_stream = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        status, response = run(input_stream.read(MAX_REQUEST_BYTES + 1))
    except (OSError, subprocess.TimeoutExpired, ValidationError):
        status, response = 5, encode_json({"accepted": False, "error": "bridge_failed"})
    target = stdout if status == 0 else stderr
    target.buffer.write(response)
    target.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
