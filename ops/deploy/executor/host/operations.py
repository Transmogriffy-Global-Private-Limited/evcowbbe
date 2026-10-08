"""Fail-closed, fixed-operation root bridge for the observed VPS2 layout.

Source-only until explicitly installed with a reviewed sudoers policy. No
arbitrary executable, path, environment assignment, unit, or script arguments
are accepted from the caller. The passive reconcile timer is NOT its caller.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from ops.deploy.control_plane import ControlPlane, ValidationError
from ops.deploy.executor.authority import GitHubAuthority, github_getter
from ops.deploy.executor.migration_plan import read_source_migrations
from ops.deploy.executor.host import contract as c


def _account_uid(account: str) -> int:
    import pwd
    return pwd.getpwnam(account).pw_uid


def _account_gid(account: str) -> int:
    import pwd
    return pwd.getpwnam(account).pw_gid


class HostOperationFailed(Exception):
    """An external side effect may already have occurred. Do not auto-retry."""


def fixed_run(argv: list[str], *, timeout: int = 45, input: bytes | None = None, limit: int = 1048576) -> bytes:
    try:
        result = subprocess.run(argv, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False, close_fds=True, env={
                                    'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                                    'LANG': 'C', 'HOME': '/root',
                                })
    except (OSError, subprocess.TimeoutExpired):
        raise HostOperationFailed('fixed host command failed or timed out') from None
    if result.returncode or len(result.stdout) > limit:
        # Never propagate stderr, it can carry network credentials or DSNs.
        raise HostOperationFailed('fixed host command returned a failure')
    return result.stdout


class VPS2Operations:
    def __init__(self, *, runner: Callable[..., bytes] = fixed_run) -> None:
        self._run = runner

    def _runuser(self, identity: str, command: list[str], *, timeout: int = 45, limit: int = 1048576) -> bytes:
        return self._run(['/usr/sbin/runuser', '-u', identity, '--', *command], timeout=timeout, limit=limit)

    def _github_authorize(self, sha: str) -> None:
        p = c.GITHUB_TOKEN
        if p.is_symlink() or not p.is_file():
            raise HostOperationFailed('GitHub read-only authority credential not provisioned')
        st = p.stat()
        if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size > 16384:
            raise HostOperationFailed('GitHub authority credential permissions invalid')
        token = p.read_text(encoding='utf-8').strip()
        if not token:
            raise HostOperationFailed('GitHub read-only credential empty')
        GitHubAuthority(github_getter(token)).verify(sha)

    def _builder_head(self, sha: str) -> None:
        """Require the exact remote prod HEAD, fetched by the builder identity."""
        c.exact_sha(sha)
        if c.SOURCE.is_symlink() or not c.SOURCE.is_dir():
            raise HostOperationFailed('builder source checkout missing or symlinked')
        self._runuser('evcow-builder', ['/usr/bin/git', '-C', str(c.SOURCE), 'fetch',
                                        '--no-tags', 'origin', 'refs/heads/prod'], timeout=80)
        actual = self._runuser('evcow-builder', ['/usr/bin/git', '-C', str(c.SOURCE),
                                                'rev-parse', 'FETCH_HEAD']).decode('ascii').strip()
        if actual != sha:
            raise HostOperationFailed('builder remote prod HEAD disagrees with expected SHA')

    def _require_gate(self, sha: str, stage: str) -> None:
        """Last-moment durable operator fence at the privileged boundary.

        Stale intentions never authorize side effects after a pause, sideline,
        or rollback hold has been added. A rollback is a recovery operation:
        its own matching hold/intent is mandatory, but pause does not block it.
        This is a point-in-time check, not cross-system atomicity; an operator
        racing a running OS syscall still needs recovery and reconciliation.
        """
        cp = ControlPlane(c.STATE_DB)
        snapshot = cp.status(verbose=True, trusted_prod_sha=sha)
        active = [r for r in snapshot['runs'] if r['status'] in {
            'running', 'migration_boundary_claimed', 'migration_uncertain', 'migration_committed'}]
        if len(active) != 1:
            raise HostOperationFailed('one active, claimed deployment run is required')
        run = active[0]
        steps = cp.run_checkpoints(run['id'])
        active_controls = snapshot['active_controls']  # key is "type", not "control_type"
        if stage == 'rollback':
            matching_holds = [x for x in active_controls if
                              x['type'] == 'rollback_hold' and x['target_sha'] == sha]
            intentions = [x for x in steps if x['stage'] == 'rollback' and
                          x['phase'] == 'intent' and x.get('previous_sha') == sha and
                          x.get('sha') == run['sha']]
            observations = [x for x in steps if x['stage'] == 'rollback' and x['phase'] == 'observed']
            if len(matching_holds) != 1 or len(intentions) != 1 or observations:
                raise HostOperationFailed('rollback lacks outstanding durable hold and intent')
            return

        # Exact admitted SHA and all current operator controls must still
        # allow normal execution. Do not reuse a gate from the coordinator.
        if run['sha'] != sha or snapshot['desired']['state'] != 'ready':
            raise HostOperationFailed('operator control or desired prod changed')
        if any(x['type'] in {'pause', 'rollback_hold'} or
               (x['type'] == 'sideline' and x['target_sha'] == sha)
               for x in active_controls):
            raise HostOperationFailed('operator control blocks privileged operation')

        if stage == 'migration':
            if run['status'] != 'migration_boundary_claimed':
                raise HostOperationFailed('migration boundary not durably claimed')
            decisions = [x for x in snapshot['migration_decisions'] if
                         x['id'] == run['migration_decision_id'] and
                         x['sha'] == sha and x['state'] == 'executing']
            if len(decisions) != 1:
                raise HostOperationFailed('exact claimed migration approval missing')
            return

        intentions = [x for x in steps if x['stage'] == stage and
                      x['phase'] == 'intent' and x.get('sha') == sha]
        observations = [x for x in steps if x['stage'] == stage and
                        x['phase'] == 'observed']
        if len(intentions) != 1 or observations:
            raise HostOperationFailed('external operation has no singular outstanding intent')

    def source(self, sha: str) -> list[dict[str, Any]]:
        self._github_authorize(sha)
        self._builder_head(sha)
        archive = self._runuser('evcow-builder', ['/usr/bin/git', '-C', str(c.SOURCE),
                                                'archive', '--format=tar', sha, 'db/migrations'], limit=8388608)
        with tempfile.TemporaryDirectory(prefix='evcow-migrations-') as temp:
            base = Path(temp) / 'db' / 'migrations'
            base.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as t:
                seen: set[str] = set()
                for member in t.getmembers():
                    if member.isdir():
                        continue
                    if not member.isfile() or not member.name.startswith('db/migrations/'):
                        raise HostOperationFailed('unexpected Git archive entry')
                    name = member.name.removeprefix('db/migrations/')
                    if '/' in name or name in seen or not name:
                        raise HostOperationFailed('unexpected or duplicate migration source')
                    seen.add(name)
                    if name.endswith('.sql'):
                        (base / name).write_bytes(t.extractfile(member).read())
            return [m.as_dict() for m in read_source_migrations(base)]

    def _temporary_env(self, sha: str):
        """Temporary root-only EnvironmentFile with the exact release revision."""
        info = c.APP_ENV.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_size > 131072
                or stat.S_IMODE(info.st_mode) & 0o007):
            raise HostOperationFailed('application EnvironmentFile failed ownership/size verification')
        original = c.APP_ENV.read_bytes()
        overlay = c.replace_revision(original, sha)
        fd, tmp = tempfile.mkstemp(prefix='evcow-revision-', dir='/run')
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as out:
                fd = -1
                out.write(overlay)
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            if fd >= 0:
                os.close(fd)
            Path(tmp).unlink(missing_ok=True)
            raise
        return Path(tmp)

    def _migrator(self, sha: str, operation: str) -> bytes:
        c.exact_sha(sha)
        if operation not in {'status', 'up', 'verify'}:
            raise ValidationError('unsupported migrator command')
        if not c.verify_release(sha, require_manifest=False):
            raise HostOperationFailed('required release migrator is missing or unsafe')
        envfile = self._temporary_env(sha)
        unit = f'evcowbbe-migrate-{uuid.uuid4().hex}.service'
        args = ['/usr/bin/systemd-run', '--quiet', '--pipe', '--wait', '--collect',
                f'--unit={unit}', '--uid=evcowbbe', '--gid=evcowbbe',
                '--working-directory=/var/lib/evcowbbe',
                f'--property=EnvironmentFile={envfile}',
                str(c.canonical_release(sha) / 'evcowbbe-migrate'), operation]
        if operation == 'status':
            args.append('--json')
        try:
            if operation == 'up':
                self._require_gate(sha, 'migration')
            return self._run(args, timeout=120 if operation == 'up' else 90, limit=1048576)
        except Exception:
            # A timeout does NOT prove the external systemd operation stopped.
            # Try to stop it; durable checkpoint + DB ledger own reconciliation.
            try:
                self._run(['/usr/bin/systemctl', 'stop', unit], timeout=15)
            except Exception:
                pass
            raise
        finally:
            envfile.unlink(missing_ok=True)

    def ledger(self) -> dict[str, Any]:
        """Version-independent, read-only ledger observer from control release.

        Never call the currently active application migrator for machine JSON:
        the accepted 2ac184c baseline predates that capability.
        """
        observer = '/opt/evcowbbe-deploy/current/bin/evcowbbe-ledger-observer'
        try:
            info = Path(observer).stat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                    not (info.st_mode & 0o111) or (stat.S_IMODE(info.st_mode) & 0o022)):
                raise HostOperationFailed('ledger observer owner or mode is unsafe')
        except OSError:
            raise HostOperationFailed('reviewed ledger observer is not installed') from None
        unit = f'evcowbbe-ledger-{uuid.uuid4().hex}.service'
        raw = self._run([
            '/usr/bin/systemd-run', '--quiet', '--pipe', '--wait', '--collect',
            f'--unit={unit}', '--uid=evcowbbe', '--gid=evcowbbe',
            '--working-directory=/var/lib/evcowbbe',
            f'--property=EnvironmentFile={c.APP_ENV}', observer,
        ], timeout=45, limit=1048576)
        try:
            def reject_duplicates(pairs):
                out = {}
                for key, value in pairs:
                    if key in out:
                        raise ValueError('duplicate observer field')
                    out[key] = value
                return out
            value = json.loads(raw, object_pairs_hook=reject_duplicates)
        except (UnicodeError, ValueError):
            raise HostOperationFailed('observer did not return exact machine JSON') from None
        if (type(value) is not dict or set(value) != {'ledger_present', 'applied'} or
                type(value['ledger_present']) is not bool or type(value['applied']) is not list):
            raise HostOperationFailed('invalid ledger observer snapshot')
        return value

    def build(self, sha: str) -> bool:
        self._require_gate(sha, 'build')
        self._github_authorize(sha)
        self._builder_head(sha)
        self._require_gate(sha, 'build')
        if c.verify_release(sha):
            return True
        target = c.canonical_release(sha)
        if target.exists() or target.is_symlink():
            raise HostOperationFailed('conflicting or partial immutable release exists')
        # The builder script is part of the root-owned immutable control plane.
        script = '/opt/evcowbbe-deploy/current/bin/evcowbbe-build-sha'
        self._runuser('evcow-builder', [script, sha], timeout=480, limit=8192)
        self._require_gate(sha, 'build')
        prepared = c.BUILD_WORK / 'prepared' / sha
        staged: Path | None = None
        try:
            if prepared.is_symlink() or not prepared.is_dir():
                raise HostOperationFailed('builder did not publish a valid pair')
            c.guarded_real_dir(c.RELEASES)
            staged = Path(tempfile.mkdtemp(prefix=f'.staging-{sha}-', dir=str(c.RELEASES)))
            for name in ('evcowbbe', 'evcowbbe-migrate'):
                source = prepared / name
                st = source.lstat()
                if not stat.S_ISREG(st.st_mode) or st.st_uid != _account_uid('evcow-builder') or not (st.st_mode & 0o111):
                    raise HostOperationFailed('builder output is not an owned regular executable')
                with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as src:
                    with (staged / name).open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                        dst.flush()
                        os.fsync(dst.fileno())
                (staged / name).chmod(0o555)
            data = {'format': 'evcowbbe-vps2-v1', 'sha': sha,
                    'server_sha256': hashlib.sha256((staged / 'evcowbbe').read_bytes()).hexdigest(),
                    'migrator_sha256': hashlib.sha256((staged / 'evcowbbe-migrate').read_bytes()).hexdigest()}
            (staged / 'RELEASE-MANIFEST').write_text(json.dumps(data, sort_keys=True) + '\n', encoding='ascii')
            (staged / 'RELEASE-MANIFEST').chmod(0o444)
            staged.chmod(0o755)
            self._require_gate(sha, 'build')
            os.rename(staged, target)
            staged = None
            return c.verify_release(sha)
        finally:
            if staged is not None:
                shutil.rmtree(staged)

    def present(self, sha: str) -> bool:
        return c.verify_release(sha)

    def active(self) -> str:
        return c.active_sha()

    def migrate(self, sha: str) -> bool:
        self._require_gate(sha, 'migration')
        self._github_authorize(sha)
        self._builder_head(sha)
        self._require_gate(sha, 'migration')
        if not c.verify_release(sha):
            raise HostOperationFailed('unverified target release')
        run = next(r for r in ControlPlane(c.STATE_DB).status(verbose=True)['runs']
                   if r['sha'] == sha and r['status'] == 'migration_boundary_claimed')
        ControlPlane(c.STATE_DB).claim_host_invocation(run['id'], 'migration', sha, 'privileged-host-bridge')
        self._migrator(sha, 'up')
        return True  # The lifecycle MUST still independently reconcile SQL ledger.

    def _switch(self, sha: str, *, stage: str) -> None:
        """A non-atomic two-resource switch: intent MUST already be durable.

        Never silently roll back on a process crash or timeout. The coordinator
        and operator must observe both resources and reconcile the result.
        """
        target = c.canonical_release(sha)
        if not c.verify_release(sha, require_manifest=False):
            raise HostOperationFailed('switch target release pair is not usable')
        info = c.APP_ENV.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o640):
            raise HostOperationFailed('application EnvironmentFile ownership or permissions changed')
        old_env = c.APP_ENV.read_bytes()
        changed = c.replace_revision(old_env, sha)
        # Recheck after file validation and directly before the first mutation.
        self._require_gate(sha, stage)
        fd, temporary = tempfile.mkstemp(prefix='.dev.env.', dir=str(c.APP_ENV.parent))
        try:
            os.fchmod(fd, 0o640)
            os.fchown(fd, 0, _account_gid('evcowbbe'))
            with os.fdopen(fd, 'wb') as out:
                out.write(changed)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, c.APP_ENV)
            temp_link = c.ROOT / f'.current-{uuid.uuid4().hex}'
            try:
                temp_link.symlink_to(f'releases/{sha}')
                os.replace(temp_link, c.CURRENT)
            finally:
                temp_link.unlink(missing_ok=True)
            self._run(['/usr/bin/systemctl', 'restart', c.SERVICE], timeout=70, limit=8192)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def activate(self, sha: str) -> bool:
        self._require_gate(sha, 'activation')
        self._github_authorize(sha)
        self._builder_head(sha)
        self._require_gate(sha, 'activation')
        if not c.verify_release(sha):
            raise HostOperationFailed('target release failed immutable manifest verification')
        # Do not switch a stale activation intent if the previous runtime link
        # changed after the lifecycle observed it.
        cp = ControlPlane(c.STATE_DB)
        active = [r for r in cp.status(verbose=True)['runs'] if r['status'] in
                  {'running', 'migration_committed'} and r['sha'] == sha]
        if len(active) != 1:
            raise HostOperationFailed('activation run not uniquely active')
        intents = [x for x in cp.run_checkpoints(active[0]['id']) if
                   x['stage'] == 'activation' and x['phase'] == 'intent']
        if len(intents) != 1 or c.active_sha() != intents[0]['previous_sha']:
            raise HostOperationFailed('prior active SHA disagrees with durable activation intent')
        cp.claim_host_invocation(active[0]['id'], 'activation', sha, 'privileged-host-bridge')
        self._switch(sha, stage='activation')
        return True  # Only /ready, /version and actual PID observation prove success.

    def verify(self, sha: str) -> bool:
        if c.active_sha() != sha:
            return False
        for property_name in ('User', 'Group'):
            observed = self._run(['/usr/bin/systemctl', 'show', '-P', property_name, c.SERVICE],
                                 limit=128).decode('ascii').strip()
            if observed != 'evcowbbe':
                return False
        pid = self._run(['/usr/bin/systemctl', 'show', '-P', 'MainPID', c.SERVICE], limit=128).decode('ascii').strip()
        if not pid.isdecimal() or int(pid) <= 1:
            return False
        actual_exe = Path(f'/proc/{pid}/exe').resolve(strict=True)
        if actual_exe != c.canonical_release(sha) / 'evcowbbe':
            return False
        if c.replace_revision(c.APP_ENV.read_bytes(), sha) != c.APP_ENV.read_bytes():
            return False
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for endpoint in ('/health/ready', '/version'):
            request = urllib.request.Request('http://127.0.0.1:18280' + endpoint, method='GET')
            with opener.open(request, timeout=5) as response:
                if response.status != 200:
                    return False
                data = response.read(4097)
                if len(data) > 4096:
                    return False
                result = json.loads(data)
                if endpoint == '/health/ready' and result.get('status') != 'ready':
                    return False
                if endpoint == '/version' and result.get('git_sha') != sha:
                    return False
        return True

    def rollback(self, previous_sha: str) -> bool:
        self._require_gate(previous_sha, 'rollback')
        cp = ControlPlane(c.STATE_DB)
        active = [r for r in cp.status(verbose=True)['runs'] if r['status'] in
                  {'running', 'migration_committed'}]
        if len(active) != 1 or c.active_sha() != active[0]['sha']:
            raise HostOperationFailed('active binary changed since rollback intent')
        if not c.verify_release(previous_sha, require_manifest=False):
            raise HostOperationFailed('rollback release pair is not available')
        # Refuse rollback when the previous binary no longer supports the
        # actual PostgreSQL schema. No down migration is attempted.
        self._migrator(previous_sha, 'verify')
        self._require_gate(previous_sha, 'rollback')
        cp.claim_host_invocation(active[0]['id'], 'rollback', previous_sha, 'privileged-host-bridge')
        self._switch(previous_sha, stage='rollback')
        return True

    def dispatch(self, operation: str, sha: str | None) -> Any:
        if operation not in c.OPS:
            raise ValidationError('invalid operation')
        if operation in ('active', 'ledger'):
            if sha is not None:
                raise ValidationError('unexpected SHA')
            return getattr(self, operation)()
        c.exact_sha(sha)
        return getattr(self, operation)(sha)
