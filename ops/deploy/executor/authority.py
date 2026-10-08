"""Independent exact-SHA GitHub branch and CI authority, not ingress order.

The caller supplies a GitHub Actions READ-ONLY credential. Never interpolate the
credential into shell arguments or log the returned HTTP exception body.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from ops.deploy.control_plane import ValidationError

SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = "Transmogriffy-Global-Private-Limited/evcowbbe"
WORKFLOW = "ci.yml"


class AuthorityUnavailable(Exception):
    """Cannot establish positive independent deployment authority. Fail closed."""


@dataclass(frozen=True)
class VerifiedAuthority:
    sha: str
    ci_run_id: int
    ci_attempt: int
    branch: str = "prod"


class GitHubAuthority:
    """Resolve prod and successful Source CI using private GitHub REST API.

    `get_json` is an injected transport for deterministic testing. Its sole
    argument is a relative API path; production uses the authenticated HTTPS
    transport in `github_getter`.
    """

    def __init__(self, get_json: Callable[[str], dict[str, Any]]) -> None:
        self._get_json = get_json

    def verify(self, expected_sha: str | None = None) -> VerifiedAuthority:
        if expected_sha is not None and not SHA_PATTERN.fullmatch(expected_sha):
            raise ValidationError("expected SHA is not a complete lowercase Git SHA")

        branch = self._request(f"/repos/{REPOSITORY}/branches/prod")
        sha = (branch.get("commit") or {}).get("sha")
        if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
            raise AuthorityUnavailable("remote prod HEAD is missing or malformed")
        if expected_sha is not None and sha != expected_sha:
            raise AuthorityUnavailable("prod HEAD moved away from the expected SHA")

        query = urllib.parse.urlencode({"branch": "prod", "head_sha": sha, "event": "push", "per_page": "100"})
        runs = self._request(f"/repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?{query}")
        entries = runs.get("workflow_runs")
        if not isinstance(entries, list):
            raise AuthorityUnavailable("CI response lacks workflow run records")
        matching: list[tuple[int, int]] = []
        for run in entries:
            if not isinstance(run, dict):
                raise AuthorityUnavailable("CI response contains malformed workflow run")
            repository = run.get("head_repository")
            if (
                run.get("head_sha") != sha
                or run.get("head_branch") != "prod"
                or run.get("name") != "Source CI"
                or run.get("event") != "push"
                or run.get("status") != "completed"
                or run.get("conclusion") != "success"
                or not isinstance(repository, dict)
                or repository.get("full_name") != REPOSITORY
            ):
                continue
            run_id, attempt = run.get("id"), run.get("run_attempt")
            if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
                raise AuthorityUnavailable("matching CI run has invalid identity")
            matching.append((run_id, attempt))
        if not matching:
            raise AuthorityUnavailable("no completed successful prod Source CI for exact HEAD")
        run_id, attempt = max(matching)
        return VerifiedAuthority(sha=sha, ci_run_id=run_id, ci_attempt=attempt)

    def _request(self, path: str) -> dict[str, Any]:
        try:
            value = self._get_json(path)
        except Exception as exc:
            raise AuthorityUnavailable("GitHub authority request failed") from None
        if not isinstance(value, dict):
            raise AuthorityUnavailable("GitHub authority response is not a JSON object")
        return value


def github_getter(token: str, *, timeout: float = 8.0) -> Callable[[str], dict[str, Any]]:
    """Construct a private GitHub REST reader; never log or expose its token.

    Use a trusted, root-readable token file in a future service installation.
    Do not pass a token through a process argument or write it to SQLite.
    """
    if not isinstance(token, str) or not token.strip() or any(ch in token for ch in "\r\n\x00"):
        raise ValidationError("GitHub credential is missing or malformed")
    if not (0 < timeout <= 30):
        raise ValidationError("GitHub timeout must be positive and bounded")

    def get_json(path: str) -> dict[str, Any]:
        if not path.startswith(f"/repos/{REPOSITORY}/") or "//" in path or "#" in path:
            raise ValidationError("unapproved GitHub API path")
        request = urllib.request.Request(
            "https://api.github.com" + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "evcowbbe-verified-deployment-authority",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
                if response.status != 200:
                    raise AuthorityUnavailable("GitHub API returned a non-success status")
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise AuthorityUnavailable("GitHub API response exceeded the byte limit")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise AuthorityUnavailable("GitHub API response is not a JSON object")
                return value
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise AuthorityUnavailable("GitHub authority request failed") from None
    return get_json
