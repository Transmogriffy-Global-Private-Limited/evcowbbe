"""Forced-command SSH ingress. It has no SQLite or deployment authority."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Mapping, TextIO

from ops.deploy.control_plane import ControlPlaneError, ValidationError
from ops.deploy.vps.common import ALLOWED_SSH_COMMAND, MAX_REQUEST_BYTES, decode_ack, encode_json, parse_exact_request


ROOT_ADMIT_AND_WAKE = "/opt/evcowbbe-deploy/current/bin/evcowbbe-deploy-admit-and-wake"


def validate_original_command(environment: Mapping[str, str]) -> None:
    if environment.get("SSH_ORIGINAL_COMMAND") != ALLOWED_SSH_COMMAND:
        raise ValidationError("SSH command is not the one permitted ingestion operation")


def forward(raw: bytes, runner: Callable[[bytes], tuple[int, bytes, bytes]]) -> bytes:
    request = parse_exact_request(raw)
    status, stdout, _stderr = runner(raw)
    if status != 0:
        raise ControlPlaneError("durable admission or wake failed")
    acknowledgement = decode_ack(stdout)
    if acknowledgement["sha"] != request["sha"]:
        raise ValidationError("acknowledgement SHA differs from admitted request")
    return stdout


def _sudo_runner(raw: bytes) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(
        ["/usr/bin/sudo", "-n", ROOT_ADMIT_AND_WAKE],
        input=raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def main(
    stdin: object = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    input_stream = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        validate_original_command(environment or os.environ)
        acknowledgement = forward(input_stream.read(MAX_REQUEST_BYTES + 1), _sudo_runner)
        stdout.buffer.write(acknowledgement)
        stdout.flush()
        return 0
    except (ControlPlaneError, OSError, subprocess.TimeoutExpired):
        stderr.write(encode_json({"accepted": False, "error": "ingress_failed"}).decode("ascii"))
        stderr.flush()
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
