"""Continuous, incident-pinned PG advisory and table-lock sidecar.

Root launches a no-argument, reviewed immutable Go executable as evcowbbe
through systemd's protected EnvironmentFile. Its SHARE table lock excludes
ordinary SQL INSERT/UPDATE/DELETE and schema DDL until explicitly released.
No SQL modification is performed. A lost guard is an ambiguous failure, not
permission to proceed or retry; this is not a distributed transaction.
"""
from __future__ import annotations

import json
import re
import os
import select
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ops.deploy.vps import interrupted_build_ledger as incident

CONTROL = Path('/opt/evcowbbe-deploy/current')
BINARY = CONTROL / 'bin/evcowbbe-recovery-ledger-guard'
ENVFILE = Path('/etc/evcowbbe/dev.env')


class PgFenceBlocked(Exception):
    """Writer exclusion could not be continuously established."""


class Guard:
    """One bounded reply per request, read directly from a nonblocking pipe.

    ``BufferedReader.readline`` may hang after select() on an incomplete
    frame; never use it on an untrusted long-lived subprocess protocol.
    """
    def __init__(self, process: subprocess.Popen):
        self.process = process
        if process.stdout is None:
            raise PgFenceBlocked('guard reply channel is missing')
        self._fd = process.stdout.fileno()
        os.set_blocking(self._fd, False)

    def _read(self, seconds: float = 45) -> dict:
        deadline = time.monotonic() + seconds
        partial = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PgFenceBlocked('database writer guard acknowledgement timed out')
            try:
                ready, _, _ = select.select([self._fd], [], [], min(remaining, 0.5))
            except (OSError, ValueError) as exc:
                raise PgFenceBlocked('guard pipe is unavailable') from exc
            if ready:
                try:
                    chunk = os.read(self._fd, 1025 - len(partial))
                except BlockingIOError:
                    continue
                except OSError as exc:
                    raise PgFenceBlocked('guard read failed') from exc
                if not chunk:
                    raise PgFenceBlocked('guard closed the acknowledgement channel')
                partial.extend(chunk)
                if len(partial) > 1024:
                    raise PgFenceBlocked('oversized guard acknowledgement')
                if b'\n' not in partial:
                    continue
                if partial[-1:] != b'\n' or partial.count(b'\n') != 1:
                    raise PgFenceBlocked('unexpected extra guard protocol bytes')
                try:
                    reply = json.loads(partial.decode('utf-8'))
                except (ValueError, UnicodeError) as exc:
                    raise PgFenceBlocked('invalid guard acknowledgement') from exc
                if (type(reply) is not dict or set(reply) !=
                        {'state', 'ledger_present', 'applied', 'fingerprint'}
                        or reply['state'] != 'held'
                        or reply['ledger_present'] is not True
                        or type(reply['applied']) is not int or reply['applied'] != 0
                        or reply['fingerprint'] != incident.EMPTY_LEDGER_SHA256):
                    raise PgFenceBlocked('guard does not positively prove the pinned ledger')
                if self.process.poll() is not None:
                    raise PgFenceBlocked('guard process exited after acknowledgement')
                return reply
            if self.process.poll() is not None:
                raise PgFenceBlocked('guard exited without positive acknowledgement')

    def ping(self) -> None:
        if self.process.poll() is not None:
            raise PgFenceBlocked('guard process is no longer running')
        try:
            self.process.stdin.write(b'ping\n')
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise PgFenceBlocked('guard ping channel unavailable') from exc
        self._read(10)


def _verify_binary() -> None:
    if not CONTROL.is_symlink() or not re.fullmatch(r'releases/[0-9a-f]{40}', os.readlink(CONTROL)):
        raise PgFenceBlocked('invalid installed immutable control release')
    target = Path('/opt/evcowbbe-deploy') / os.readlink(CONTROL)
    try:
        directory = target.lstat()
        binaries = (target / 'ops/deploy/vps/bin').lstat()
    except OSError as exc:
        raise PgFenceBlocked('control release directory is unavailable') from exc
    if any(not stat.S_ISDIR(item.st_mode) or item.st_uid != 0
           or item.st_mode & 0o022 for item in (directory, binaries)):
        raise PgFenceBlocked('control release directories are not immutable root-owned')
    try:
        st = BINARY.stat()
        env = ENVFILE.lstat()
    except OSError as exc:
        raise PgFenceBlocked('guard binary or existing application environment missing') from exc
    if (BINARY.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid != 0
            or not st.st_mode & 0o111 or st.st_mode & 0o022
            or not stat.S_ISREG(env.st_mode) or env.st_uid != 0 or env.st_mode & 0o022):
        raise PgFenceBlocked('guard executable/environment ownership or mode unsafe')


@contextmanager
def continuous_postgres_writer_fence() -> Iterator[Guard]:
    """Production entrypoint never accepts credentials, SQL, paths or values."""
    if os.name != 'posix' or os.geteuid() != 0:
        raise PgFenceBlocked('root on Linux is required')
    _verify_binary()
    unit = f'evcowbbe-recovery-guard-{uuid.uuid4().hex}.service'
    command = [
        '/usr/bin/systemd-run', '--quiet', '--pipe', '--wait', '--collect',
        f'--unit={unit}', '--uid=evcowbbe', '--gid=evcowbbe',
        '--working-directory=/var/lib/evcowbbe',
        f'--property=EnvironmentFile={ENVFILE}', str(BINARY),
    ]
    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, close_fds=True,
            env={'LANG': 'C', 'PATH': '/usr/bin:/bin'},
        )
    except OSError as exc:
        raise PgFenceBlocked('could not launch fixed PostgreSQL guard') from exc
    try:
        guard = Guard(proc)
        guard._read()
        yield guard
    finally:
        # Never leak the external lock-holder on return or a raised exception.
        try:
            if proc.poll() is None:
                try:
                    proc.stdin.write(b'release\n')
                    proc.stdin.flush()
                except (OSError, ValueError):
                    pass
                proc.stdin.close()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
        finally:
            if proc.stdout:
                proc.stdout.close()
