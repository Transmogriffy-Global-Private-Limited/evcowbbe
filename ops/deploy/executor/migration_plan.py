"""Deterministic, immutable migration planning from actual ledger metadata.

This module NEVER opens PostgreSQL or executes SQL. Ledger observations MUST
come from a trusted direct database query, not CLI `status` counts or ingress.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ops.deploy.control_plane import ValidationError, canonical_json

_FILENAME = re.compile(r"([0-9]+)_([a-z0-9][a-z0-9_]*)\.sql\Z")
_ATTEMPT = re.compile(r"^[ \t]*--[ \t]*evcowbbe:min-compatible-binary-version[^\r\n]*\r?$", re.M)
_DIRECTIVE = re.compile(r"^[ \t]*--[ \t]*evcowbbe:min-compatible-binary-version=([0-9]+)[ \t]*\r?$", re.M)
_CHECKSUM = re.compile(r"[a-f0-9]{64}\Z")
_NAME = re.compile(r"[a-z0-9][a-z0-9_]*\Z")
_SHA = re.compile(r"[a-f0-9]{40}\Z")


@dataclass(frozen=True)
class MigrationIdentity:
    version: int
    name: str
    checksum: str
    min_compatible_binary_version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "name": self.name,
            "checksum": self.checksum,
            "min_compatible_binary_version": self.min_compatible_binary_version,
        }


@dataclass(frozen=True)
class MigrationPlan:
    target_sha: str
    ledger_fingerprint: str
    plan_fingerprint: str
    pending: tuple[MigrationIdentity, ...]
    binary_schema_version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_sha": self.target_sha,
            "starting_ledger_fingerprint": self.ledger_fingerprint,
            "plan_fingerprint": self.plan_fingerprint,
            "pending": [m.as_dict() for m in self.pending],
            "pending_count": len(self.pending),
            "binary_schema_version": self.binary_schema_version,
            "requires_approval": bool(self.pending),
        }


def read_source_migrations(directory: str | Path) -> tuple[MigrationIdentity, ...]:
    """Match the Go migrator's file naming, bytes/checksum, and floor semantics."""
    root = Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ValidationError("embedded migration source directory must exist and not be a symlink")
    identities: list[MigrationIdentity] = []
    seen: set[int] = set()
    for path in sorted(root.iterdir()):
        if not path.name.endswith(".sql"):
            continue
        if not path.is_file() or path.is_symlink():
            raise ValidationError("migration file must be regular and not a symlink")
        match = _FILENAME.fullmatch(path.name)
        if match is None:
            raise ValidationError("invalid migration filename")
        version = int(match.group(1))
        if version <= 0 or version > 9223372036854775807 or version in seen:
            raise ValidationError("migration version is duplicated or outside positive int64")
        raw = path.read_bytes()
        if not raw:
            raise ValidationError("migration file is empty")
        text = raw.decode("utf-8", "surrogateescape")
        attempts = _ATTEMPT.findall(text)
        matches = list(_DIRECTIVE.finditer(text))
        if len(attempts) != 1 or len(matches) != 1:
            raise ValidationError("migration compatibility directive is absent, duplicated, or malformed")
        floor = int(matches[0].group(1))
        if floor > version:
            raise ValidationError("migration compatibility floor exceeds its version")
        seen.add(version)
        identities.append(MigrationIdentity(version, match.group(2), hashlib.sha256(raw).hexdigest(), floor))
    return tuple(sorted(identities, key=lambda m: m.version))


def _validate_ledger(ledger: Sequence[Mapping[str, Any]]) -> tuple[MigrationIdentity, ...]:
    validated: list[MigrationIdentity] = []
    seen: set[int] = set()
    for row in ledger:
        if not isinstance(row, Mapping) or set(row) != {
            "version", "name", "checksum", "min_compatible_binary_version"
        }:
            raise ValidationError("ledger identity row is incomplete or contains unknown fields")
        version = row["version"]
        floor = row["min_compatible_binary_version"]
        name = row["name"]
        checksum = row["checksum"]
        if (type(version) is not int or not 0 < version <= 9223372036854775807
                or version in seen or type(floor) is not int or floor < 0 or floor > version
                or not isinstance(name, str) or not _NAME.fullmatch(name)
                or not isinstance(checksum, str) or not _CHECKSUM.fullmatch(checksum)):
            raise ValidationError("ledger identity metadata is invalid or duplicated")
        seen.add(version)
        validated.append(MigrationIdentity(version, name, checksum, floor))
    return tuple(sorted(validated, key=lambda m: m.version))


def plan_migrations(
    target_sha: str,
    source: Sequence[MigrationIdentity],
    observed_ledger: Sequence[Mapping[str, Any]],
    *, ledger_present: bool,
) -> MigrationPlan:
    """Plan from source identities and observed, already-applied PostgreSQL rows.

    `ledger_present=False` is deliberately blocked even when no SQL files exist:
    first-time ledger initialization is an explicit, separate operator action.
    """
    if not isinstance(target_sha, str) or not _SHA.fullmatch(target_sha):
        raise ValidationError("target SHA must be an exact 40-character lowercase Git SHA")
    if ledger_present is not True:
        raise ValidationError("PostgreSQL migration ledger is absent or not positively verified")
    if any(not isinstance(m, MigrationIdentity) for m in source):
        raise ValidationError("source migration identities are malformed")
    known = {m.version: m for m in source}
    if len(known) != len(source) or any(
        type(m.version) is not int or not 0 < m.version <= 9223372036854775807
        or not isinstance(m.name, str) or not _NAME.fullmatch(m.name)
        or not isinstance(m.checksum, str) or not _CHECKSUM.fullmatch(m.checksum)
        or type(m.min_compatible_binary_version) is not int
        or not 0 <= m.min_compatible_binary_version <= m.version
        for m in source
    ):
        raise ValidationError("source migration identities are duplicated or malformed")
    observed = _validate_ledger(observed_ledger)
    applied = {m.version: m for m in observed}
    schema_version = max(known, default=0)
    top_applied = max(applied, default=0)

    for version, row in applied.items():
        known_migration = known.get(version)
        if known_migration is not None and known_migration != row:
            raise ValidationError("applied migration differs in name, checksum or compatibility floor")
        if known_migration is None and version <= schema_version:
            raise ValidationError("unknown applied migration within target binary schema range")
        if known_migration is None and row.min_compatible_binary_version > schema_version:
            raise ValidationError("future applied migration is incompatible with target binary")
    for version in known:
        if version < top_applied and version not in applied:
            raise ValidationError("migration history has a missing lower version below applied history")

    pending = tuple(known[v] for v in sorted(known) if v not in applied)
    ledger_json = [m.as_dict() for m in observed]
    ledger_fingerprint = hashlib.sha256(canonical_json(ledger_json).encode("ascii")).hexdigest()
    fingerprint_payload = {
        "target_sha": target_sha,
        "starting_ledger_fingerprint": ledger_fingerprint,
        "pending": [m.as_dict() for m in pending],
    }
    plan_fingerprint = hashlib.sha256(canonical_json(fingerprint_payload).encode("ascii")).hexdigest()
    return MigrationPlan(target_sha, ledger_fingerprint, plan_fingerprint, pending, schema_version)


def require_exact_approval(plan: MigrationPlan, decisions: Sequence[Mapping[str, Any]]) -> str | None:
    """Return decision ID only when a pending plan has active exact approval."""
    if not plan.pending:
        return None
    approved = [
        d for d in decisions
        if d.get("sha") == plan.target_sha
        and d.get("plan_fingerprint") == plan.plan_fingerprint
        and d.get("required_migration_count") == len(plan.pending)
        and d.get("state") == "approved"
    ]
    if len(approved) != 1 or not isinstance(approved[0].get("id"), str):
        raise ValidationError("pending migration plan requires one active exact-plan approval")
    return approved[0]["id"]
