"""Shared HTTP edge validation for the C1 Driver and Admin routers."""
import json
import re

from cow.common.errors import reject
from cow.services import idempotency


def key(request):
    value = request.headers.get("Idempotency-Key", "")
    if not 16 <= len(value) <= 128 or "," in value or any(ord(c) < 33 or ord(c) > 126 for c in value):
        reject(428 if not value else 422, "IDEMPOTENCY_KEY_REQUIRED", "A unique 16-128 character Idempotency-Key is required")
    return value


def version(request):
    value = request.headers.get("If-Match", "")
    if not value:
        reject(428, "PRECONDITION_REQUIRED", "A current quoted If-Match version is required")
    if not re.fullmatch(r'"(0|[1-9][0-9]*)"', value):
        reject(400, "INVALID_IF_MATCH", "If-Match must be a quoted decimal version")
    return int(value[1:-1])


def check_json(request):
    if request.content_type.split(";", 1)[0].lower() != "application/json":
        reject(415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")
    if int(request.META.get("CONTENT_LENGTH") or 0) > 32768 or len(request.body) > 32768:
        reject(413, "REQUEST_TOO_LARGE", "JSON request is too large")

    def unique_pairs(items):
        obj = {}
        for k, v in items:
            if k in obj:
                raise ValueError("Duplicate JSON member")
            obj[k] = v
        return obj

    try:
        json.loads(request.body, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeDecodeError):
        reject(422, "AMBIGUOUS_JSON", "Malformed or ambiguous JSON request")


def mutate(request, scope, payload, fn, *, status=200, expected_version=None):
    check_json(request)
    idem = key(request)
    data = payload.model_dump(mode="json", exclude_unset=True)
    if expected_version is not None:
        data["_expected_version"] = expected_version
    return idempotency.execute(
        request.auth, scope, idem, data,
        lambda: (status, {"data": fn()}), retention_hours=168,
    )
