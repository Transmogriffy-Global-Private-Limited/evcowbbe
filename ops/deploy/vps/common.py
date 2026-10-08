"""Strict bounded JSON admission and acknowledgement wire formats."""

from __future__ import annotations

import json
import re
from typing import Any

from ops.deploy.control_plane import ControlPlane, ValidationError, canonical_json

MAX_REQUEST_BYTES = 4_096
MAX_ACK_BYTES = 4_096
STATE_DB_PATH = "/var/lib/evcowbbe-deploy/orchestrator.sqlite3"
ALLOWED_SSH_COMMAND = "evcowbbe-deploy-ingest-v1"


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValidationError("non-finite JSON constants are forbidden")


def _decode_one(raw: bytes, limit: int, kind: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > limit:
        raise ValidationError(f"{kind} must be nonempty and within its size limit")
    # Exactly one canonical JSON object with at most one terminating LF.
    body = raw[:-1] if raw.endswith(b"\n") else raw
    if not body or body.endswith((b"\n", b"\r")):
        raise ValidationError(f"{kind} has forbidden trailing data")
    try:
        text = body.decode("utf-8", "strict")
        value = json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f"{kind} must be exactly one valid JSON object") from exc
    # Do not accept leading whitespace or other noncanonical wire framing.
    if not text.startswith("{") or not text.endswith("}") or not isinstance(value, dict):
        raise ValidationError(f"{kind} must be exactly one JSON object without outer whitespace")
    return value


def parse_exact_request(raw: bytes, state_db: str = STATE_DB_PATH) -> dict[str, str]:
    """Decode one exact push request; Burner 1 enforces field semantics."""
    value = _decode_one(raw, MAX_REQUEST_BYTES, "ingress request")
    validated, _, _ = ControlPlane(state_db)._validate_request(value)
    return validated


def encode_json(value: dict[str, Any]) -> bytes:
    encoded = canonical_json(value).encode("ascii") + b"\n"
    if len(encoded) > MAX_ACK_BYTES:
        raise ValidationError("ingress acknowledgement exceeds its size limit")
    return encoded


def decode_ack(raw: bytes) -> dict[str, Any]:
    value = _decode_one(raw, MAX_ACK_BYTES, "ingress acknowledgement")
    if set(value) != {"accepted", "created", "request_id", "sha"}:
        raise ValidationError("unexpected acknowledgement fields")
    if value["accepted"] is not True or not isinstance(value["created"], bool):
        raise ValidationError("not a successful admission acknowledgement")
    if not isinstance(value["request_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["request_id"]):
        raise ValidationError("invalid acknowledgement request ID")
    if not isinstance(value["sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["sha"]):
        raise ValidationError("invalid acknowledgement SHA")
    return value
