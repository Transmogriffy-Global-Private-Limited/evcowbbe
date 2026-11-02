"""Request headers and response contracts; Go operations are protected by the frozen wire contract."""
import json
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse

from cow import go_wire


class RequestSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        try:
            request.cow_request_id = str(UUID(incoming)) if incoming else str(uuid4())
        except ValueError:
            request.cow_request_id = str(uuid4())

        path = request.path
        origin = request.headers.get("Origin", "")
        method = request.method.upper()
        go_op = go_wire.match_operation(path, method)
        go_cors = go_wire.is_go_cors_path(path)

        def failure(status, code, message):
            return JsonResponse({"error": {"code": code, "message": message,
                "request_id": request.cow_request_id, "details": {}}}, status=status)

        result = None
        if method == "OPTIONS" and go_cors and not origin:
            result = failure(405, "METHOD_NOT_ALLOWED", "Method not allowed")
        elif method == "OPTIONS" and go_cors and origin:
            reqmethod = request.headers.get("Access-Control-Request-Method", "")
            reqheaders = request.headers.get("Access-Control-Request-Headers", "")
            verdict = go_wire.check_preflight(origin, reqmethod, reqheaders)
            result = failure(*verdict) if verdict else JsonResponse({}, status=204)
        elif go_op is not None and go_wire.is_unauthenticated_go_route(path) and method in {"POST", "PUT", "PATCH", "DELETE"}:
            content_type = request.content_type
            if content_type.split(";", 1)[0].lower() != "application/json":
                result = failure(415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")
            elif go_op.get("requestBody") is not None:
                try:
                    if int(request.META.get("CONTENT_LENGTH") or 0) > go_wire.MAX_GO_JSON_BYTES:
                        result = failure(400, "VALIDATION_FAILED", "Invalid JSON request")
                    else:
                        verdict = go_wire.check_request(path, method, content_type, request.body,
                                                        has_request_body=True)
                        if verdict:
                            result = failure(*verdict)
                except (ValueError, RequestDataTooBig):
                    result = failure(400, "VALIDATION_FAILED", "Invalid JSON request")

        if result is None and method == "OPTIONS" and path.startswith("/api/v1/"):
            if origin not in settings.COW_CORS_ORIGINS:
                result = failure(403, "ORIGIN_FORBIDDEN", "Origin not allowed")
            else:
                result = JsonResponse({}, status=204)
        if result is None:
            result = self.get_response(request)

        # Django/Ninja's route-level 404/405 fallback is an HTML/plain-text
        # response. Go returns the normal JSON error envelope for every frozen
        # method/path, including a wrong method on an otherwise valid path.
        if go_wire.is_go_path(path) and result.status_code >= 400:
            try:
                decoded_error = json.loads(result.content)
                has_error_envelope = isinstance(decoded_error, dict) and isinstance(
                    decoded_error.get("error"), dict
                )
            except (UnicodeError, ValueError, TypeError):
                has_error_envelope = False
            if not has_error_envelope:
                error_codes = {
                    400: ("VALIDATION_FAILED", "Invalid request"),
                    404: ("NOT_FOUND", "Endpoint not found"),
                    405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
                    415: ("UNSUPPORTED_MEDIA_TYPE", "Expected application/json"),
                }
                code, message = error_codes.get(
                    result.status_code, ("INTERNAL_ERROR", "Internal error")
                )
                result = failure(result.status_code, code, message)

        # Go's Server.err sets this fixed retry hint for ErrRate and
        # ErrCooldown. All current Go-path 429s arise from those cases; keep
        # the transport contract centralized without changing domain logic.
        if go_wire.is_go_path(path) and result.status_code == 429:
            result.setdefault("Retry-After", "60")

        if go_op is not None and not getattr(result, "streaming", False) and 200 <= result.status_code < 300:
            try:
                raw = json.loads(result.content)
                adjusted = go_wire.success_body(path, method, result.status_code, raw)
                if adjusted != raw:
                    result.content = json.dumps(adjusted, separators=(",", ":")).encode("utf-8")
                    if "Content-Length" in result:
                        del result["Content-Length"]
            except (UnicodeError, ValueError, TypeError):
                pass

        result["X-Request-ID"] = request.cow_request_id
        result["Cache-Control"] = "no-store"
        result["X-Content-Type-Options"] = "nosniff"
        result["Referrer-Policy"] = "no-referrer"
        result["X-Frame-Options"] = "DENY"
        if settings.ENV != "development":
            result["Strict-Transport-Security"] = "max-age=31536000"

        if go_cors and origin:
            # Align Go's explicit six development origins, independent of the
            # optional CORS settings for Python-only endpoints.
            result["Vary"] = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers" \
                if method == "OPTIONS" else "Origin"
            if origin in go_wire.GO_DEVELOPMENT_ORIGINS:
                result["Access-Control-Allow-Origin"] = origin
                result["Access-Control-Expose-Headers"] = "ETag, X-Request-ID, Retry-After"
                if method == "OPTIONS" and result.status_code == 204:
                    result["Access-Control-Allow-Methods"] = go_wire.GO_CORS_METHODS
                    result["Access-Control-Allow-Headers"] = go_wire.GO_CORS_HEADERS
                    result["Access-Control-Max-Age"] = "600"
        elif origin in settings.COW_CORS_ORIGINS:
            result["Access-Control-Allow-Origin"] = origin
            result["Vary"] = "Origin"
            result["Access-Control-Allow-Methods"] = "GET,POST,PATCH,PUT,DELETE,OPTIONS"
            result["Access-Control-Allow-Headers"] = "Content-Type,Authorization,Idempotency-Key,If-Match,X-Request-ID"
            result["Access-Control-Max-Age"] = "300"
        return result
