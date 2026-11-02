"""Go EVCOWBBE HTTP compatibility for operations present in the frozen Go API.

Only Go-owned method/path pairs are adapted. The Python extension routes are
intentionally outside this boundary. Go's actual handler behavior takes
precedence over stale OpenAPI descriptions (for example, /version).
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "docs/contracts/auth.openapi.yaml"
GO_DEVELOPMENT_ORIGINS = frozenset({
    "http://localhost:5173", "http://localhost:8080", "http://localhost:8081",
    "http://127.0.0.1:5173", "http://127.0.0.1:8080", "http://127.0.0.1:8081",
})
GO_CORS_HEADERS = (
    "Accept, Authorization, Cache-Control, Content-Type, Idempotency-Key, "
    "If-Match, Pragma, X-Request-ID, X-Requested-With"
)
GO_CORS_METHODS = "GET, HEAD, POST, PUT, PATCH, DELETE"
VALID_PREFLIGHT_METHODS = frozenset(GO_CORS_METHODS.replace(",", "").split())
VALID_PREFLIGHT_HEADERS = frozenset(h.strip().lower() for h in GO_CORS_HEADERS.split(","))
API_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
MAX_GO_JSON_BYTES = 32768


@lru_cache(maxsize=1)
def go_routes() -> tuple[tuple[re.Pattern[str], str, str, dict], ...]:
    doc = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    routes = []
    for path, definition in doc["paths"].items():
        expr = re.escape(path)
        expr = re.sub(r"\\\{[A-Za-z_][A-Za-z0-9_]*\\\}", r"[^/]+", expr)
        pattern = re.compile(r"^" + expr + r"$")
        for method, operation in definition.items():
            if method in API_METHODS:
                routes.append((pattern, method.upper(), path, operation))
    if len(routes) != 82 or len({(m, p) for _, m, p, _ in routes}) != 82:
        raise RuntimeError("Frozen Go API operation catalog changed; refuse implicit compatibility drift")
    return tuple(routes)


def match_operation(path: str, method: str) -> dict | None:
    for pattern, registered_method, _, operation in go_routes():
        if method.upper() == registered_method and pattern.fullmatch(path):
            return operation
    return None


@lru_cache(maxsize=1)
def go_openapi() -> dict:
    """Load the frozen published declaration once for documentation overlays."""
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def declared_header_parameters(path: str, method: str) -> tuple[dict, ...]:
    """Return only Go-declared manual headers used by the Django boundary.

    This deliberately does not copy arbitrary operation metadata. The two
    headers below are read and validated by the shared mutation helpers; their
    constraints therefore describe current runtime behavior as well as the
    published Go contract.
    """
    doc = go_openapi()
    operation = doc.get("paths", {}).get(path, {}).get(method.lower())
    if not isinstance(operation, dict):
        return ()
    path_item = doc["paths"][path]
    components = doc.get("components", {}).get("parameters", {})
    headers = []
    for parameter in [*path_item.get("parameters", []), *operation.get("parameters", [])]:
        if "$ref" in parameter and parameter["$ref"].startswith("#/components/parameters/"):
            parameter = components.get(parameter["$ref"].rsplit("/", 1)[-1], {})
        if (
            parameter.get("in") == "header"
            and parameter.get("name") in {"Idempotency-Key", "If-Match"}
        ):
            headers.append(deepcopy(parameter))
    return tuple(headers)


def is_go_path(path: str) -> bool:
    return any(pattern.fullmatch(path) for pattern, _, _, _ in go_routes())


def is_unauthenticated_go_route(path: str) -> bool:
    """Go parses these endpoints' JSON before principal authentication."""
    if path.startswith("/api/v1/auth/"):
        return path in {
            "/api/v1/auth/verify-email", "/api/v1/auth/resend-verification",
            "/api/v1/auth/forgot-password", "/api/v1/auth/reset-password",
        }
    m = re.fullmatch(r"/api/v1/(app|driver|admin)/auth/(.+)", path)
    if not m:
        return False
    plane, action = m.groups()
    return action in {"login", "verify-otp", "refresh", "2fa/resend"} or (
        plane != "admin" and action == "register"
    )


def is_go_cors_path(path: str) -> bool:
    # Go applies corsEndpoint to its API prefix, diagnostics and OpenAPI.
    return is_go_path(path) or path in {
        "/health/live", "/health/ready", "/version", "/openapi.yaml"
    }


def check_preflight(origin: str, requested_method: str, requested_headers: str):
    if origin not in GO_DEVELOPMENT_ORIGINS:
        return 403, "CORS_ORIGIN_FORBIDDEN", "Origin is not allowed"
    if requested_method not in VALID_PREFLIGHT_METHODS:
        return 400, "CORS_METHOD_INVALID", "Preflight method is not allowed"
    if requested_headers and any(
        not h.strip() or h.strip().lower() not in VALID_PREFLIGHT_HEADERS
        for h in requested_headers.split(",")
    ):
        return 400, "CORS_HEADERS_INVALID", "Preflight headers are not allowed"
    return None


def check_request(path: str, method: str, content_type: str, body: bytes,
                  *, has_request_body: bool) -> tuple[int, str, str] | None:
    """Go API's request-level JSON decoder rules (not user business validation).

    Go accepts duplicate JSON keys except where pricing applies a separate
    duplicate-key guard; do not accidentally impose broader constraints.
    """
    if match_operation(path, method) is None:
        return None
    if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        return 415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json"
    if not has_request_body:
        return None
    if len(body) > MAX_GO_JSON_BYTES:
        return 400, "VALIDATION_FAILED", "Invalid JSON request"
    try:
        parsed = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(parsed, dict):
            raise ValueError("object expected")
    except (ValueError, UnicodeDecodeError, TypeError):
        return 400, "VALIDATION_FAILED", "Invalid JSON request"
    return None


def success_body(path: str, method: str, status: int, body: dict[str, Any]) -> dict[str, Any]:
    """Normalize only Go legacy success response envelopes.

    Do not create data if the original operation returned an error or a
    malformed/absent envelope. The caller must preserve original status/headers.
    """
    if status < 200 or status >= 300 or match_operation(path, method) is None:
        return body
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        return body
    data = body["data"]
    if path.startswith("/api/v1/auth/"):
        if path.endswith(("/verify-email", "/reset-password")):
            return {"data": {"ok": True}}
        if path.endswith(("/resend-verification", "/forgot-password")):
            return {"data": {"accepted": True}}
    m = re.fullmatch(r"/api/v1/(app|driver|admin)/(.*)", path)
    if not m:
        return body
    plane, endpoint = m.groups()
    if endpoint == "me":
        if "account_id" not in data:
            return body
        return {"data": {
            "id": data["account_id"], "email": data["email"], "plane": plane,
            "principal_status": data["status"],
            "email_verified": data["email_verified"],
            "two_factor_enabled": data["two_factor_enabled"],
        }}
    if endpoint in {"auth/refresh", "auth/verify-otp"}:
        # Django services currently return {two_factor_required:false,tokens:{...}}.
        # Go returns the Tokens structure at data directly for these operations.
        tokens = data.get("tokens")
        if isinstance(tokens, dict) and not data.get("two_factor_required", True):
            return {"data": tokens}
    if endpoint in {
        "auth/logout", "auth/logout-all", "auth/password/change",
        "auth/2fa/resend", "auth/2fa/enable/confirm", "auth/2fa/disable/confirm",
    }:
        return {"data": {"ok": True}}
    if plane == "admin" and endpoint.startswith("rbac/"):
        if method.upper() in {"PUT", "POST", "DELETE"}:
            return {"data": {"ok": True}}
        if endpoint == "rbac/me":
            # account_id must be added in the handler: this view only has the
            # authorized request principal, not the request auth context.
            return body
        if re.fullmatch(r"rbac/accounts/[^/]+", endpoint):
            return {"data": {k: v for k, v in data.items() if k != "principal_id"}}
    return body
