"""Run the *actual* guard under transient systemd against disposable CI PostgreSQL.

No VPS endpoints, secrets or deployment commands. Never invokes SQLite recovery.
"""
from __future__ import annotations

import json
import os
import pwd
import grp
import select
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

PINNED_EMPTY = '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'


def main() -> int:
    if (
        sys.platform != 'linux'
        or os.environ.get('RECOVERY_TEST_ISOLATED') != '1'
        or os.environ.get('GITHUB_ACTIONS') != 'true'
    ):
        raise RuntimeError('refusing PostgreSQL/systemd test outside isolated GitHub runner')
    dsn = os.environ.get('RECOVERY_TEST_POSTGRES_URL', '')
    u = urlparse(dsn)
    if (
        u.scheme != 'postgres' or u.netloc != 'recovery_ci:recovery_ci@127.0.0.1:5432'
        or u.path != '/evcowbbe_recovery_ci' or u.query != 'sslmode=disable'
        or u.fragment
    ):
        raise RuntimeError('unsafe or non-disposable PostgreSQL test fixture')
    temp_dir = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    binary = temp_dir / 'evcowbbe-recovery-ledger-guard-ci'
    if not binary.is_file() or binary.is_symlink() or not os.access(binary, os.X_OK):
        raise RuntimeError('reviewed Go guard build missing from runner temp')

    # The transient systemd manager reads this local CI-only EnvironmentFile.
    fd, env_path = tempfile.mkstemp(prefix='evcowbbe-recovery-fixture-', dir=temp_dir)
    os.fchmod(fd, 0o600)
    os.write(fd, ('DATABASE_URL=' + dsn + '\n').encode('ascii'))
    os.fsync(fd)
    os.close(fd)

    unit = 'evcowbbe-recovery-stdio-ci-' + uuid.uuid4().hex
    identity = pwd.getpwuid(os.getuid())
    command = [
        '/usr/bin/sudo', '-n', '/usr/bin/systemd-run',
        '--quiet', '--pipe', '--wait', '--collect',
        '--unit=' + unit, '--uid=' + identity.pw_name,
        '--gid=' + grp.getgrgid(identity.pw_gid).gr_name,
        '--working-directory=' + str(temp_dir),
        '--property=EnvironmentFile=' + env_path,
        str(binary),
    ]
    proc = None
    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, close_fds=True,
        )
        pipe = proc.stdout.fileno()
        os.set_blocking(pipe, False)

        def read_ack(seconds=12):
            deadline = time.monotonic() + seconds
            data = bytearray()
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    raise RuntimeError('real systemd-run guard acknowledgement timed out')
                readable, _, _ = select.select([pipe], [], [], min(remain, 0.5))
                if readable:
                    try:
                        fragment = os.read(pipe, 1025 - len(data))
                    except BlockingIOError:
                        continue
                    if not fragment:
                        raise RuntimeError('real systemd-run guard pipe closed')
                    data.extend(fragment)
                    if len(data) > 1024:
                        raise RuntimeError('oversized guard output')
                    if data.endswith(b'\n'):
                        decoded = json.loads(data)
                        if (
                            type(decoded) is not dict or
                            decoded != {
                                'state': 'held', 'ledger_present': True,
                                'applied': 0, 'fingerprint': PINNED_EMPTY,
                            }
                        ):
                            raise RuntimeError('invalid pinned-identity acknowledgement')
                        return
                if proc.poll() is not None:
                    raise RuntimeError('guard exited unexpectedly under systemd-run')

        read_ack()
        proc.stdin.write(b'ping\n')
        proc.stdin.flush()
        read_ack()
        proc.stdin.write(b'release\n')
        proc.stdin.flush()
        proc.stdin.close()
        if proc.wait(timeout=15) != 0:
            raise RuntimeError('systemd-run guard did not terminate cleanly')
        print('SYSTEMD_REAL_GUARD_STREAMING_VERIFIED_DISPOSABLE_POSTGRES')
        return 0
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        # Explicitly stop only our random, disposable unit if a failure left it
        # alive after the wrapper died. Never address any production unit.
        subprocess.run(
            ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'stop', unit],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=12, check=False,
        )
        Path(env_path).unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
