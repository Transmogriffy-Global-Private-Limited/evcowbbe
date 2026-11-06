"""URLconf-owned operations that complement Ninja's generated API schema."""

from __future__ import annotations

from copy import deepcopy

from cow.common.contracts import GO_ERROR_ENVELOPE_SCHEMA
from cow.go_wire import declared_header_parameters, match_operation

REQUEST_ID_HEADER = {
    "description": "Request correlation UUID returned by every response.",
    "schema": {"type": "string", "format": "uuid"},
}


def _diagnostic_success(value: dict) -> dict:
    return {
        "description": "Go-compatible diagnostic response.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "data": {
                            "type": "object",
                            "properties": value,
                            "required": sorted(value),
                            "additionalProperties": False,
                        }
                    },
                    "required": ["data"],
                    "additionalProperties": False,
                }
            }
        },
    }

RETRY_AFTER_HEADER = {
    "description": "Seconds until a rate-limited request may be retried.",
    "schema": {"type": "integer", "minimum": 0},
}


def add_go_error_contracts(schema: dict) -> dict:
    """Declare the centralized error boundary for each frozen Go operation.

    The published Go contract supplies each operation's *declared status set*;
    the schema and response headers come from the source-verified Django
    middleware/exception boundary. This is documentation of a shared runtime
    capability, not a claim that every business error is reachable on every
    request. Route-family tests remain responsible for reachability.
    """
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            go_operation = match_operation(path, method)
            if go_operation is None:
                continue
            responses = operation.setdefault("responses", {})
            for status in go_operation.get("responses", {}):
                status = str(status)
                if not status.isdigit() or int(status) < 400:
                    continue
                headers = {"X-Request-ID": deepcopy(REQUEST_ID_HEADER)}
                if status == "429":
                    headers["Retry-After"] = deepcopy(RETRY_AFTER_HEADER)
                responses[status] = {
                    "description": "Go-compatible error response.",
                    "content": {
                        "application/json": {"schema": deepcopy(GO_ERROR_ENVELOPE_SCHEMA)}
                    },
                    "headers": headers,
                }
    return schema


def add_go_mutation_header_contracts(schema: dict) -> dict:
    """Expose source-verified manual mutation headers in generated OpenAPI."""
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or match_operation(path, method) is None:
                continue
            parameters = operation.setdefault("parameters", [])
            existing = {
                (parameter.get("in"), parameter.get("name"))
                for parameter in parameters
                if isinstance(parameter, dict)
            }
            for parameter in declared_header_parameters(path, method):
                key = (parameter["in"], parameter["name"])
                if key not in existing:
                    parameters.append(parameter)
    return schema


def add_standard_response_headers(schema: dict) -> dict:
    """Document headers installed by RequestSecurityMiddleware.

    This derives from the Django middleware contract, not from the frozen Go
    YAML.  The public `/openapi.yaml` must describe the headers callers receive
    on both success and error responses.
    """
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for response in operation.get("responses", {}).values():
                if isinstance(response, dict):
                    response.setdefault("headers", {})["X-Request-ID"] = deepcopy(REQUEST_ID_HEADER)
    return schema


ETAG_RESPONSES = {
    ("post", "/api/v1/admin/vehicles"),
    ("post", "/api/v1/admin/chargers"),
    ("get", "/api/v1/admin/vehicles/{vehicle_id}"),
    ("patch", "/api/v1/admin/vehicles/{vehicle_id}"),
    ("post", "/api/v1/admin/vehicles/{vehicle_id}/deactivate"),
    ("post", "/api/v1/admin/vehicles/{vehicle_id}/reactivate"),
    ("get", "/api/v1/admin/chargers/{charger_id}"),
    ("patch", "/api/v1/admin/chargers/{charger_id}"),
    ("post", "/api/v1/admin/chargers/{charger_id}/deactivate"),
    ("post", "/api/v1/admin/chargers/{charger_id}/reactivate"),
    ("post", "/api/v1/admin/chargers/{charger_id}/connectors"),
    ("patch", "/api/v1/admin/chargers/{charger_id}/connectors/{connector_id}"),
    ("post", "/api/v1/admin/pricing/policies"),
    ("get", "/api/v1/admin/pricing/policies/{policy_id}"),
    ("post", "/api/v1/admin/pricing/policies/{policy_id}/publish"),
    ("post", "/api/v1/admin/pricing/policies/{policy_id}/withdraw"),
}


def add_source_verified_etag_contracts(schema: dict) -> dict:
    """Document ETags actually produced by fleet/pricing detail/mutations."""
    for (method, path) in ETAG_RESPONSES:
        operation = schema.get("paths", {}).get(path, {}).get(method)
        if not operation:
            continue
        for status, response in operation.get("responses", {}).items():
            if str(status).startswith("2"):
                response.setdefault("headers", {})["ETag"] = {
                    "description": "Strong resource version ETag.",
                    "schema": {"type": "string"},
                }
    return schema


def add_source_verified_pricing_query_contracts(schema: dict) -> dict:
    """Describe status validation without changing the existing 422 path."""
    operation = schema.get("paths", {}).get("/api/v1/admin/pricing/policies", {}).get("get")
    if operation is None:
        # The generic editor was retired in favor of Zone-only tariffs.
        # No query parameter exists to decorate when that route is hidden.
        return schema
    if not isinstance(operation, dict):
        raise ValueError("Missing pricing list operation")
    matches = [p for p in operation.get("parameters", [])
               if p.get("in") == "query" and p.get("name") == "status"]
    if len(matches) != 1 or matches[0].get("required", False):
        raise ValueError("Pricing status parameter missing or unexpectedly required")
    shape = matches[0].get("schema")
    if not isinstance(shape, dict) or shape.get("type") != "string":
        raise ValueError("Unexpected pricing status schema")
    shape["enum"] = ["", "DRAFT", "PUBLISHED", "WITHDRAWN"]
    return schema


def add_diagnostics(schema: dict) -> dict:
    """Add the three registered Django diagnostic operations to Ninja output."""
    paths = schema.setdefault("paths", {})
    paths.update(
        {
            "/health/live": {
                "get": {
                    "tags": ["Diagnostics"],
                    "operationId": "health_live",
                    "summary": "Liveness probe",
                    "security": [],
                    "responses": {
                        "200": _diagnostic_success({"status": {"const": "live", "type": "string"}})
                    },
                }
            },
            "/health/ready": {
                "get": {
                    "tags": ["Diagnostics"],
                    "operationId": "health_ready",
                    "summary": "Database readiness probe",
                    "security": [],
                    "responses": {
                        "200": _diagnostic_success({"status": {"const": "ready", "type": "string"}}),
                        "503": {"description": "Database unavailable"},
                    },
                }
            },
            "/version": {
                "get": {
                    "tags": ["Diagnostics"],
                    "operationId": "version",
                    "summary": "Logical API version",
                    "security": [],
                    "responses": {
                        "200": _diagnostic_success(
                            {"version": {"const": "admin-pricing-policy-1", "type": "string"}}
                        )
                    },
                }
            },
        }
    )
    return add_standard_response_headers(
        add_source_verified_pricing_query_contracts(add_source_verified_etag_contracts(add_go_mutation_header_contracts(add_go_error_contracts(schema))))
    )
