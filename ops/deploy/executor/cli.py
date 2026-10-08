"""Read-only Burner 3 preflight CLI. No deploy, root escalation or mutation."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Sequence

from ops.deploy.control_plane import ControlPlane, ControlPlaneError, ValidationError, canonical_json
from ops.deploy.executor.authority import AuthorityUnavailable, GitHubAuthority, github_getter
from ops.deploy.executor.migration_plan import read_source_migrations
from ops.deploy.executor.preflight import evaluate_preflight

MAX_LEDGER_JSON_BYTES = 1_048_576


def read_ledger_snapshot(path: Path) -> tuple[bool, list[dict]]:
    if not path.is_file() or path.is_symlink():
        raise ValidationError("trusted migrator ledger snapshot must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_LEDGER_JSON_BYTES + 1)
    if len(raw) > MAX_LEDGER_JSON_BYTES:
        raise ValidationError("trusted migrator ledger snapshot exceeds byte limit")
    def reject_duplicates(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValidationError("duplicate JSON member in migrator snapshot")
            result[name] = value
        return result
    try:
        result = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("trusted migrator ledger snapshot is not JSON") from None
    if (not isinstance(result, dict) or set(result) != {"ledger_present", "applied"}
            or type(result["ledger_present"]) is not bool or not isinstance(result["applied"], list)):
        raise ValidationError("trusted migrator ledger snapshot has an unsupported schema")
    return result["ledger_present"], result["applied"]


def read_token_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError("GitHub read-only token file is missing or symlinked")
    st = path.stat()
    if os.name == "posix" and (stat.S_IMODE(st.st_mode) & 0o077):
        raise ValidationError("GitHub read-only token file must not be group/world accessible")
    if st.st_size > 16_384:
        raise ValidationError("GitHub token file is oversized")
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ValidationError("GitHub read-only token file cannot be read") from None
    return token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only, fail-closed COW deployment preflight")
    parser.add_argument("--state-db", required=True)
    parser.add_argument("--migration-dir", required=True)
    parser.add_argument("--ledger-json", required=True, help="trusted output of target migrator status --json")
    parser.add_argument("--github-token-file", required=True, help="read-only GitHub credential file; never print")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        token = read_token_file(Path(args.github_token_file))
        source = read_source_migrations(Path(args.migration_dir))
        ledger_present, applied = read_ledger_snapshot(Path(args.ledger_json))
        authority = GitHubAuthority(github_getter(token))
        outcome = evaluate_preflight(
            authority, ControlPlane(args.state_db), source, applied, ledger_present=ledger_present,
        )
    except (AuthorityUnavailable, ControlPlaneError, OSError) as exc:
        # Never emit exception messages for HTTP, file reads or subprocesses:
        # API/provider strings and configuration paths may contain secrets.
        print(canonical_json({"state": "blocked", "reason": type(exc).__name__}), file=sys.stderr)
        return 5
    print(canonical_json(outcome.as_dict()))
    return 0 if outcome.state == "ready" else 4


if __name__ == "__main__":
    raise SystemExit(main())
