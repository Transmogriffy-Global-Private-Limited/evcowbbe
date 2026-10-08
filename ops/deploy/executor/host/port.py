"""ExecutionPort client with a single fixed, no-arguments sudo bridge."""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from ops.deploy.control_plane import ValidationError
from ops.deploy.executor.host.contract import exact_sha
from ops.deploy.executor.migration_plan import MigrationIdentity, _validate_ledger

BRIDGE = '/opt/evcowbbe-deploy/current/bin/evcowbbe-deploy-host-op'


class HostPortError(Exception):
    """The operation was not positively acknowledged; side effects may exist."""


class VPS2ExecutionPort:
    def __init__(self, *, runner: Callable[..., Any] = subprocess.run) -> None:
        self.runner = runner

    def _request(self, op: str, sha: str | None = None, *, timeout: int = 600) -> Any:
        body = json.dumps({'op': op, 'sha': sha}, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
        try:
            result = self.runner(['/usr/bin/sudo', '-n', BRIDGE], input=body,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise HostPortError('fixed privileged operation unavailable') from None
        if result.returncode or len(result.stdout) > 1048576:
            raise HostPortError('fixed privileged operation failed')
        try:
            def unique_pairs(pairs):
                out = {}
                for k, v in pairs:
                    if k in out:
                        raise ValueError('duplicate acknowledgement key')
                    out[k] = v
                return out
            response = json.loads(result.stdout, object_pairs_hook=unique_pairs)
        except (ValueError, UnicodeError):
            raise HostPortError('fixed bridge returned invalid JSON') from None
        if type(response) is not dict or set(response) != {'ok', 'result'} or response['ok'] is not True:
            raise HostPortError('fixed bridge acknowledgement invalid')
        return response['result']

    def source_migrations(self, sha: str):
        value = self._request('source', exact_sha(sha), timeout=120)
        if type(value) is not list:
            raise HostPortError('invalid source migration data')
        try:
            return tuple(MigrationIdentity(**x) for x in value)
        except (TypeError, ValueError):
            raise HostPortError('invalid source migration identities') from None

    def ledger(self):
        result = self._request('ledger', timeout=120)
        if (type(result) is not dict or set(result) != {'ledger_present', 'applied'}
                or type(result['ledger_present']) is not bool or type(result['applied']) is not list):
            raise HostPortError('invalid ledger snapshot')
        _validate_ledger(result['applied'])
        return result['ledger_present'], result['applied']

    def build_release(self, sha: str):
        if self._request('build', exact_sha(sha)) is not True:
            raise HostPortError('release build not positively acknowledged')

    def release_present(self, sha: str) -> bool:
        value = self._request('present', exact_sha(sha))
        if type(value) is not bool:
            raise HostPortError('release observation invalid')
        return value

    def active_release(self) -> str:
        value = self._request('active')
        return exact_sha(value)

    def activate_release(self, sha: str):
        if self._request('activate', exact_sha(sha), timeout=180) is not True:
            raise HostPortError('activation not positively acknowledged')

    def run_migrations(self, sha: str):
        if self._request('migrate', exact_sha(sha), timeout=180) is not True:
            raise HostPortError('migrations not positively acknowledged')

    def verify_runtime(self, sha: str) -> bool:
        value = self._request('verify', exact_sha(sha), timeout=45)
        if type(value) is not bool:
            raise HostPortError('runtime verification response invalid')
        return value

    def rollback_release(self, previous_sha: str):
        if self._request('rollback', exact_sha(previous_sha), timeout=180) is not True:
            raise HostPortError('rollback not positively acknowledged')
