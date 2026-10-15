"""Root-only, non-mutating, fenced host inspection of interrupted builds.

NO SQLite transition or retry is available here. The context deliberately
keeps the executor's own kernel flock held while its caller verifies other
facts and (in a future separately reviewed implementation) commits recovery.
Host evidence returned *after* leaving the context is no longer authoritative.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

if os.name == 'posix':
    import fcntl
    import pwd
else:
    fcntl = None
    pwd = None

LOCK = Path('/opt/evcowbbe-deploy/executor.lock')
APP_ROOT = Path('/srv/evcowbbe')
WORK = APP_ROOT / 'build-work'
RELEASES = APP_ROOT / 'releases'
PROC = Path('/proc')
EXECUTOR = 'evcowbbe-deploy-executor.service'
TIMER = 'evcowbbe-deploy-executor.timer'
SHA = re.compile(r'[0-9a-f]{40}\Z')


class HostRecoveryBlocked(Exception):
    """Quiescence or positive runtime evidence is not available."""


@dataclass(frozen=True)
class FencedHostObservation:
    candidate_sha: str
    previous_sha: str
    # SQL identity is deliberately NOT approved by this host-only observer.
    result: str = 'host_quiescence_only_not_authorized_to_reconcile'


def _directory(path: Path, owner: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise HostRecoveryBlocked('expected recovery directory absent') from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise HostRecoveryBlocked('unsafe recovery directory ownership, type or permissions')


def _lock_executor() -> int:
    """Open the exact immutable lock inode used by execute_once.py."""
    if fcntl is None:
        raise HostRecoveryBlocked('Linux kernel flock is required')
    _directory(LOCK.parent, 0)
    try:
        fd = os.open(LOCK, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise HostRecoveryBlocked('executor lock unavailable') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o444:
            raise HostRecoveryBlocked('executor lock owner or mode mismatch')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HostRecoveryBlocked('another executor or recovery owns the lock') from exc
        return fd
    except BaseException:
        os.close(fd)
        raise


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ['/usr/bin/systemctl', *args], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=12, check=False, close_fds=True,
            env={'LANG': 'C', 'PATH': '/usr/bin:/bin'},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostRecoveryBlocked('systemd observation unavailable') from exc
    if len(result.stdout) > 4096:
        raise HostRecoveryBlocked('systemd response exceeds bound')
    try:
        return result.returncode, result.stdout.decode('ascii').strip()
    except UnicodeError as exc:
        raise HostRecoveryBlocked('malformed systemd response') from exc


def _quiescent(systemctl: Callable[..., tuple[int, str]]) -> None:
    rc, raw = systemctl('show', '--no-pager', '-p', 'ActiveState', '-p', 'SubState',
                        '-p', 'MainPID', '-p', 'ControlPID', EXECUTOR)
    data: dict[str, str] = {}
    for line in raw.splitlines():
        if '=' not in line:
            raise HostRecoveryBlocked('malformed executor properties')
        key, value = line.split('=', 1)
        if key in data:
            raise HostRecoveryBlocked('duplicate executor property')
        data[key] = value
    if (rc != 0 or set(data) != {'ActiveState', 'SubState', 'MainPID', 'ControlPID'}
            or data['ActiveState'] not in {'inactive', 'failed'}
            or data['SubState'] not in {'dead', 'failed'}
            or data['MainPID'] != '0' or data['ControlPID'] != '0'):
        raise HostRecoveryBlocked('executor process state is not quiescent')
    enabled_rc, enabled = systemctl('is-enabled', TIMER)
    active_rc, active = systemctl('is-active', TIMER)
    if enabled != 'disabled' or enabled_rc not in (0, 1) or active != 'inactive' or active_rc not in (0, 3):
        raise HostRecoveryBlocked('executor timer is not disabled and inactive')


def _no_artifacts(candidate: str, builder_uid: int) -> None:
    _directory(APP_ROOT, 0)
    _directory(WORK, builder_uid)
    _directory(WORK / 'prepared', builder_uid)
    _directory(RELEASES, 0)
    # scandir includes dangling links and unexpected staging names.
    for parent in (WORK, WORK / 'prepared', RELEASES):
        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    if candidate in entry.name:
                        raise HostRecoveryBlocked('candidate or staging artifact remains')
        except OSError as exc:
            raise HostRecoveryBlocked('candidate artifact inventory incomplete') from exc


def _no_builder_processes(candidate: str) -> None:
    """Conservatively detect root, builder and escaped service processes."""
    if os.name != 'posix':
        raise HostRecoveryBlocked('Linux procfs required')
    build_marker = f'.build-{candidate}-'
    stage_marker = f'.staging-{candidate}-'
    try:
        with os.scandir(PROC) as entries:
            for item in entries:
                if not item.name.isdecimal():
                    continue
                directory = PROC / item.name
                try:
                    argv = (directory / 'cmdline').read_bytes()
                    cgroup = (directory / 'cgroup').read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    continue  # Process disappeared while being examined.
                except OSError as exc:
                    raise HostRecoveryBlocked('process inventory inaccessible') from exc
                try:
                    cwd = os.readlink(directory / 'cwd')
                except (FileNotFoundError, ProcessLookupError):
                    # An exited process or kernel thread cannot be a live Go
                    # builder, but never ignore an identified executor cgroup
                    # or the fixed builder script just because cwd is gone.
                    if (b'evcowbbe-build-sha' in argv
                            or b'evcowbbe-deploy-executor.service' in cgroup):
                        raise HostRecoveryBlocked('executor or builder process lacks cwd')
                    continue
                except OSError as exc:
                    raise HostRecoveryBlocked('process working directory inaccessible') from exc
                if (b'evcowbbe-build-sha' in argv
                        or b'evcowbbe-deploy-executor.service' in cgroup
                        or build_marker in cwd or stage_marker in cwd):
                    raise HostRecoveryBlocked('builder or executor process remains')
    except OSError as exc:
        raise HostRecoveryBlocked('procfs inventory unavailable') from exc


def _builder_uid() -> int:
    if pwd is None:
        raise HostRecoveryBlocked('POSIX identities required')
    try:
        return pwd.getpwnam('evcow-builder').pw_uid
    except KeyError as exc:
        raise HostRecoveryBlocked('builder identity missing') from exc


@contextmanager
def fenced_build_observation(
    candidate: str, previous: str, *,
    operations_factory: Callable | None = None,
    lock_acquire: Callable[[], int] = _lock_executor,
    systemctl: Callable[..., tuple[int, str]] = _systemctl,
    processes: Callable[[str], None] = _no_builder_processes,
    artifacts: Callable[[str, int], None] = _no_artifacts,
    builder_uid: Callable[[], int] = _builder_uid,
) -> Iterator[FencedHostObservation]:
    """Fenced, read-only host proof. No recovery authority is exposed.

    The injected callables are only for unit tests. Production callers must
    accept defaults and must not accept externally supplied facts or paths.
    A future recovery coordinator must check the actual PostgreSQL ledger,
    SQLite events, and all writer fences under this same held lock.
    """
    if os.name != 'posix' or os.geteuid() != 0:
        raise HostRecoveryBlocked('recovery host inspection requires Linux root')
    if (type(candidate) is not str or type(previous) is not str
            or not SHA.fullmatch(candidate) or not SHA.fullmatch(previous)
            or candidate == previous):
        raise HostRecoveryBlocked('invalid or equal candidate and prior SHA')
    fd = lock_acquire()
    try:
        if operations_factory is None:
            from ops.deploy.executor.host.operations import VPS2Operations
            operations_factory = VPS2Operations
        host = operations_factory()
        uid = builder_uid()
        _quiescent(systemctl)
        processes(candidate)
        artifacts(candidate, uid)
        if host.active() != previous or host.verify(previous) is not True:
            raise HostRecoveryBlocked('original runtime identity and readiness not verified')
        # Confirm the subject stayed quiescent across the runtime observation.
        processes(candidate)
        artifacts(candidate, uid)
        _quiescent(systemctl)
        yield FencedHostObservation(candidate, previous)
    finally:
        os.close(fd)
