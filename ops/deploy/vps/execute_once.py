"""One-shot VPS2 executor, *inert* until a root-owned control-release arm exists.

Runs as evcow-orchestrator with an exclusive kernel flock. All privileged host
operations and authenticated GitHub checks cross the fixed sudo bridge. Never
replays a possibly interrupted external effect: the lifecycle owns recovery.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Callable

if os.name == "posix":
    import fcntl
    import pwd
else:
    fcntl = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

from ops.deploy.control_plane import ControlPlane
from ops.deploy.executor.host.port import VPS2ExecutionPort, VPS2VerifiedAuthority
from ops.deploy.executor.lifecycle import DeploymentLifecycle

CONTROL = Path('/opt/evcowbbe-deploy/current')
ARM = Path('/opt/evcowbbe-deploy/executor-arm')
STATE = Path('/var/lib/evcowbbe-deploy')
DB = STATE / 'orchestrator.sqlite3'
LOCK = Path('/opt/evcowbbe-deploy/executor.lock')


class ExecutorDisabled(Exception):
    pass


def require_arm(control: Path = CONTROL, arm: Path = ARM) -> None:
    """A root-owned, release-SHA-bound opt-in gate, not a credential."""
    if not control.is_symlink() or arm.is_symlink():
        raise ExecutorDisabled('executor has no valid arm')
    import re
    target = os.readlink(control)
    if not re.fullmatch(r'releases/[0-9a-f]{40}', target):
        raise ExecutorDisabled('invalid control release pointer')
    try:
        st = arm.stat()
    except OSError:
        raise ExecutorDisabled('executor not armed') from None
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o444:
        raise ExecutorDisabled('executor arm owner or mode mismatch')
    if arm.read_bytes() != b'evcowbbe-executor-v1 ' + target.removeprefix('releases/').encode('ascii') + b'\n':
        raise ExecutorDisabled('executor arm bound to another control release')


def exclusive_lock(lock: Path = LOCK) -> int:
    """Flock a root-owned, non-replaceable lock file opened read-only.

    The state owner cannot unlink/recreate a lock in its writable SQLite dir.
    Linux flock permits an exclusive lock on an O_RDONLY descriptor.
    """
    directory = lock.parent.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
            or directory.st_mode & 0o022):
        raise ExecutorDisabled('unsafe lock directory')
    fd = os.open(lock, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0
                or stat.S_IMODE(st.st_mode) != 0o444):
            raise ExecutorDisabled('unsafe executor lock file')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def execute_once(*, control: Path = CONTROL, arm: Path = ARM,
                 state: Path = STATE, db: Path = DB, lock: Path = LOCK,
                 lifecycle_factory: Callable | None = None) -> dict:
    identity = pwd.getpwnam('evcow-orchestrator')
    if os.geteuid() != identity.pw_uid:
        raise ExecutorDisabled('executor must run as evcow-orchestrator')
    require_arm(control, arm)
    fd = exclusive_lock(lock)
    try:
        # Recheck *after* obtaining the lock: re-arming or upgrade during a
        # blocked invocation must not authorize code from the wrong release.
        require_arm(control, arm)
        if lifecycle_factory is None:
            port = VPS2ExecutionPort()
            lifecycle = DeploymentLifecycle(
                VPS2VerifiedAuthority(port), ControlPlane(str(db)), port,
                executor_identity='evcow-orchestrator',
            )
        else:
            lifecycle = lifecycle_factory()
        result = lifecycle.execute_once()
        return result.as_dict()
    finally:
        os.close(fd)


def main() -> int:
    if len(sys.argv) != 1:
        print('{"error":"executor_accepts_no_arguments"}', file=sys.stderr)
        return 2
    try:
        result = execute_once()
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        # An unresolved external effect is an operational failure, never a
        # successful timer tick. The journal retains the exact durable state.
        return 7 if result['state'] == 'manual_reconciliation_required' else 0
    except ExecutorDisabled:
        print('{"error":"executor_not_authorized"}', file=sys.stderr)
        return 6
    except BlockingIOError:
        print('{"error":"executor_already_running"}', file=sys.stderr)
        return 75
    except Exception:
        # Never expose credential, environment, DSN or upstream provider text.
        print('{"error":"executor_failed"}', file=sys.stderr)
        return 5


if __name__ == '__main__':
    raise SystemExit(main())
