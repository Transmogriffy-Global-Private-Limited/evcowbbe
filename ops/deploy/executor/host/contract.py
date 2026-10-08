"""Fixed VPS2 host contract; no caller-supplied filesystem paths or commands."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from ops.deploy.control_plane import ValidationError

SHA = re.compile(r"[0-9a-f]{40}\Z")
ROOT = Path('/srv/evcowbbe')
SOURCE = ROOT / 'source'
BUILD_WORK = ROOT / 'build-work'
RELEASES = ROOT / 'releases'
CURRENT = ROOT / 'current'
APP_ENV = Path('/etc/evcowbbe/dev.env')
SERVICE = 'evcowbbe-dev.service'
STATE_DB = '/var/lib/evcowbbe-deploy/orchestrator.sqlite3'
GITHUB_TOKEN = Path('/etc/evcowbbe-deploy/github-readonly.token')
OPS = frozenset({'source', 'ledger', 'build', 'present', 'active', 'activate', 'migrate', 'verify', 'rollback'})


def exact_sha(value: Any) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValidationError('expected exact lowercase 40-character Git SHA')
    return value


def decode_request(raw: bytes) -> tuple[str, str | None]:
    if not raw or len(raw) > 4096:
        raise ValidationError('invalid bounded operation request')
    def unique_pairs(pairs):
        value = {}
        for k, v in pairs:
            if k in value:
                raise ValidationError('duplicate operation field')
            value[k] = v
        return value
    try:
        obj = json.loads(raw, object_pairs_hook=unique_pairs)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ValidationError('operation request must be valid JSON') from None
    if type(obj) is not dict or set(obj) != {'op', 'sha'} or obj['op'] not in OPS:
        raise ValidationError('unknown operation or request fields')
    sha = obj['sha']
    if obj['op'] in ('active', 'ledger'):
        if sha is not None:
            raise ValidationError('read-only operation does not accept a SHA')
    else:
        exact_sha(sha)
    return obj['op'], sha


def canonical_release(sha: str) -> Path:
    return RELEASES / exact_sha(sha)


def guarded_real_dir(path: Path, *, owner: int | None = 0) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValidationError('required directory missing or symbolic link')
    info = path.lstat()
    if owner is not None and info.st_uid != owner:
        raise ValidationError('required directory has unexpected owner')
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ValidationError('required directory is writable by group or others')


def verify_release(sha: str, *, require_manifest: bool = True) -> bool:
    path = canonical_release(sha)
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        guarded_real_dir(path)
        for name in ('evcowbbe', 'evcowbbe-migrate'):
            f = path / name
            st = f.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or not (st.st_mode & 0o111) or (st.st_mode & 0o022):
                return False
        if require_manifest:
            manifest = path / 'RELEASE-MANIFEST'
            st = manifest.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or (st.st_mode & 0o022) or st.st_size > 4096:
                return False
            data = json.loads(manifest.read_bytes())
            if (type(data) is not dict or set(data) != {'format', 'sha', 'server_sha256', 'migrator_sha256'}
                    or data['format'] != 'evcowbbe-vps2-v1' or data['sha'] != sha):
                return False
            import hashlib
            for file_name, field in [('evcowbbe', 'server_sha256'), ('evcowbbe-migrate', 'migrator_sha256')]:
                if data[field] != hashlib.sha256((path / file_name).read_bytes()).hexdigest():
                    return False
        return True
    except (OSError, ValueError, TypeError, ValidationError):
        return False


def active_sha() -> str:
    if not CURRENT.is_symlink():
        raise ValidationError('current application release symlink is missing')
    target = os.readlink(CURRENT)
    # Old VPS2 releases already use root-owned releases/<SHA>, but verify the
    # target against one exact canonical layout rather than resolving any path.
    match = re.fullmatch(r'(?:releases/|/srv/evcowbbe/releases/)([0-9a-f]{40})', target)
    if match is None:
        raise ValidationError('current symlink is not a fixed relative release target')
    sha = match.group(1)
    if not verify_release(sha, require_manifest=False):
        raise ValidationError('active release pair is missing or unsafe')
    return sha


def replace_revision(contents: bytes, sha: str) -> bytes:
    """Preserve all other secret environment bytes, rejecting ambiguous revision lines."""
    exact_sha(sha)
    if len(contents) > 131072 or b'\x00' in contents:
        raise ValidationError('application EnvironmentFile is invalid or oversized')
    lines = contents.splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if re.match(rb'^[ \t]*BUILD_REVISION[ \t]*=', line)]
    if len(positions) != 1:
        raise ValidationError('exactly one BUILD_REVISION setting is required')
    i = positions[0]
    ending = b'\r\n' if lines[i].endswith(b'\r\n') else b'\n' if lines[i].endswith(b'\n') else b''
    # Enforce a simple value syntax, not shell expansion or a quoted fragment.
    before = lines[i].rstrip(b'\r\n')
    match = re.fullmatch(rb'[ \t]*BUILD_REVISION[ \t]*=[ \t]*([0-9a-f]{40})[ \t]*', before)
    if not match:
        raise ValidationError('BUILD_REVISION must have one exact SHA value')
    lines[i] = b'BUILD_REVISION=' + sha.encode('ascii') + ending
    return b''.join(lines)
