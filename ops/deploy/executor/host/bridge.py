"""Root-only single-request JSON command entrypoint; no shell dispatcher."""
from __future__ import annotations

import json
import os
import sys
from typing import TextIO

from ops.deploy.control_plane import ValidationError
from ops.deploy.executor.host.contract import decode_request
from ops.deploy.executor.host.operations import VPS2Operations


def main(stdin=None, stdout: TextIO | None = None, stderr: TextIO | None = None,
         *, ops: VPS2Operations | None = None, enforce_root: bool = True) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if enforce_root and os.geteuid() != 0:
        stderr.write('{"ok":false,"error":"bridge_requires_root"}\n')
        return 5
    try:
        if len(sys.argv) != 1 and enforce_root:
            raise ValidationError('bridge accepts no arguments')
        operation, sha = decode_request(stdin.read(4097))
        response = (ops or VPS2Operations()).dispatch(operation, sha)
        stdout.write(json.dumps({'ok': True, 'result': response}, separators=(',', ':'), sort_keys=True) + '\n')
        stdout.flush()
        return 0
    except Exception:
        # Failures are deliberately redacted. Never leak GitHub credentials,
        # EnvironmentFile values, path details or upstream HTTP errors.
        stderr.write('{"ok":false,"error":"host_operation_failed"}\n')
        stderr.flush()
        return 5


if __name__ == '__main__':
    raise SystemExit(main())
