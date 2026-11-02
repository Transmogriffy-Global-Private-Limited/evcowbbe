"""Compare the frozen Go wire contract with Django's generated OpenAPI.

The historical V2 report is immutable evidence. This checker writes a new,
versioned report and deliberately reports meaningful request, response,
parameter, error, security, and header differences. It never changes routes,
database state, or the historical report.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/verification/go-python-parity-20261102/PARITY_REPORT.json"
HISTORICAL_REPORT = ROOT / "docs/verification/go_python_contract_diff.json"
CHECKER_VERSION = "2026-11-02.5"
METHODS = {"get", "post", "put", "patch", "delete"}
SCHEMA_KEYWORDS = (
    "type",
    "format",
    "enum",
    "const",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
)


def _resolve(schema: Any, definitions: dict[str, Any], depth: int = 0) -> Any:
    """Resolve local references while preserving sibling constraints."""
    if not isinstance(schema, dict) or depth > 40:
        return schema
    value = copy.deepcopy(schema)
    reference = value.pop("$ref", None)
    if reference:
        if not reference.startswith("#/components/schemas/"):
            value["$ref"] = reference
            return value
        name = reference.rsplit("/", 1)[1]
        base = _resolve(definitions.get(name, {}), definitions, depth + 1)
        if not isinstance(base, dict):
            return base
        value = {**base, **value}
    return value


def _canonical_schema(schema: Any, definitions: dict[str, Any], depth: int = 0) -> Any:
    """Canonicalize comparable JSON Schema/OpenAPI representations."""
    if depth > 40:
        return {"error": "schema_depth_exceeded"}
    value = _resolve(schema, definitions, depth)
    if not isinstance(value, dict):
        return value
    if "allOf" in value:
        return {"allOf": [_canonical_schema(v, definitions, depth + 1) for v in value["allOf"]]}
    for composition in ("anyOf", "oneOf"):
        if composition in value:
            members = [_canonical_schema(v, definitions, depth + 1) for v in value[composition]]
            # OpenAPI 3.0 nullable and JSON Schema's string|null anyOf mean the
            # same thing only for this exact two-member scalar representation.
            # JSON Schema constraints on type T are inapplicable to null,
            # so the following is a semantics-preserving representation.
            if composition == "anyOf" and len(members) == 2:
                nulls = [m for m in members if m == {"type": "null"}]
                nonnull = [m for m in members if isinstance(m, dict) and m != {"type": "null"}]
                if len(nulls) == len(nonnull) == 1 and isinstance(nonnull[0].get("type"), str):
                    return {**nonnull[0], "type": sorted(["null", nonnull[0]["type"]])}
            return {composition: members}
    result = {key: value[key] for key in SCHEMA_KEYWORDS if key in value}
    if value.get("nullable"):
        current = result.get("type")
        if isinstance(current, str):
            result["type"] = ["null", current]
        elif isinstance(current, list) and "null" not in current:
            result["type"] = sorted([*current, "null"])
    if isinstance(result.get("type"), list):
        result["type"] = sorted(result["type"])
    if "properties" in value:
        result["properties"] = {
            key: _canonical_schema(child, definitions, depth + 1)
            for key, child in sorted(value["properties"].items())
        }
    if "required" in value:
        result["required"] = sorted(value["required"])
    if "items" in value:
        result["items"] = _canonical_schema(value["items"], definitions, depth + 1)
    if "additionalProperties" in value:
        child = value["additionalProperties"]
        result["additionalProperties"] = (
            _canonical_schema(child, definitions, depth + 1) if isinstance(child, dict) else child
        )
    return result


def _body_schema(operation: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    body = operation.get("requestBody", {})
    if not isinstance(body, dict):
        return {"invalid": "requestBody"}
    if "$ref" in body:
        return {"external_ref": body["$ref"]}
    return {
        media: _canonical_schema(content.get("schema"), definitions)
        for media, content in sorted(body.get("content", {}).items())
    }


def _pinned_go_request_schema(operation: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Apply only source-proven corrections to stale published request docs."""
    if "/auth/logout" in operation:
        # server.go's post() validates content type only and never calls decode.
        return {}
    corrected = copy.deepcopy(schema)
    if operation == "POST /api/v1/auth/verify-email" or operation.endswith("/auth/refresh"):
        field = "token" if operation.endswith("verify-email") else "refresh_token"
        body = corrected.get("application/json", {})
        body.get("properties", {}).get(field, {}).pop("pattern", None)
        if "required" in body:
            body["required"] = [item for item in body["required"] if item != field]
            if not body["required"]:
                body.pop("required")
    if operation == "POST /api/v1/auth/reset-password":
        body = corrected.get("application/json", {})
        body.get("properties", {}).get("token", {}).pop("pattern", None)
        if "required" in body:
            body["required"] = [
                item for item in body["required"] if item not in {"token", "new_password"}
            ]
            if not body["required"]:
                body.pop("required")
    # The published fleet YAML omitted nullable and positive-int bounds.
    # internal/fleet/service.go's pinned ValidateFields catalog is the
    # executable authority. These corrections are restricted to exact routes
    # and fields; never generalize away arbitrary schema discrepancies.
    path = operation.split(" ", 1)[-1]
    resource = None
    if operation.startswith(("POST ", "PATCH ")):
        if path in {"/api/v1/admin/vehicles", "/api/v1/admin/vehicles/{vehicle_id}"}:
            resource = "vehicle"
        elif path in {"/api/v1/admin/chargers", "/api/v1/admin/chargers/{charger_id}"}:
            resource = "charger"
        elif path in {"/api/v1/admin/chargers/{charger_id}/connectors", "/api/v1/admin/chargers/{charger_id}/connectors/{connector_id}"}:
            resource = "connector"
    if resource is not None:
        catalog = {
            "vehicle": {
                "nullable": {"registration_number", "variant", "vehicle_type", "ownership_type", "registration_valid_until", "insurance_valid_until", "notes"},
                "nonempty_text": {"variant", "vehicle_type", "ownership_type", "notes"},
                "positive_int": set(),
            },
            "charger": {
                "nullable": {"serial_number", "rated_power_w", "protocol_family", "protocol_version", "hal_identity", "notes"},
                "nonempty_text": {"protocol_family", "protocol_version", "hal_identity", "notes"},
                "positive_int": {"rated_power_w"},
            },
            "connector": {
                "nullable": set(),
                "nonempty_text": set(),
                "positive_int": {"connector_number", "max_power_w"},
            },
        }[resource]
        body = corrected.get("application/json", {})
        for name, field in body.get("properties", {}).items():
            if name in catalog["nullable"] and "type" in field:
                field["type"] = sorted(["null", field["type"]])
            if name in catalog["nonempty_text"]:
                field["minLength"] = 1
            if name in catalog["positive_int"]:
                field["maximum"] = 2147483647
    # Pinned pricing StrictJSON decodes into DraftRequest/Rule Go structs:
    # omitted or null priority becomes int zero; omitted/null conditions
    # becomes nil; nullable string dimensions decode as empty strings.
    # Go pricing.Validate permits absent priority and optional blank/null
    # dimensions, while validScope requires a nonempty key. The published
    # YAML has stale required/non-null/minimum declarations.
    if operation == "POST /api/v1/admin/pricing/policies":
        body = corrected.get("application/json", {})
        props = body.get("properties", {})
        rules = props.get("rules", {}).get("items", {})
        if (not isinstance(rules, dict) or not isinstance(rules.get("properties"), dict)
                or "priority" not in rules["properties"]):
            raise ValueError("Pinned pricing draft schema changed unexpectedly")
        rule_props = rules["properties"]
        for field in ("component", "basis", "target", "tax_class", "conditions", "priority"):
            shape = rule_props.get(field)
            if not isinstance(shape, dict) or not isinstance(shape.get("type"), str):
                raise ValueError(f"Pinned pricing draft {field} schema changed")
            shape["type"] = sorted(["null", shape["type"]])
        required = rules.get("required", [])
        if "priority" not in required:
            raise ValueError("Pinned pricing YAML no longer declares priority required")
        rules["required"] = [name for name in required if name != "priority"]
        scope_key = props.get("scope", {}).get("properties", {}).get("key", {})
        if scope_key.get("type") != "string" or "minLength" in scope_key:
            raise ValueError("Pinned pricing scope.key schema changed")
        scope_key["minLength"] = 1
    return corrected


def _pinned_go_success_response(operation: str, response: dict[str, Any]) -> dict[str, Any]:
    """Correct three diagnostics whose YAML lags pinned server.go behavior."""
    constants = {
        "GET /health/live": ("status", "live"),
        "GET /health/ready": ("status", "ready"),
        "GET /version": ("version", "admin-pricing-policy-1"),
    }
    item = constants.get(operation)
    if item is None:
        # The published fleet YAML advertises ETag on four operations which
        # return unversioned objects. Pinned fleet.go's fleetMutation calls
        # fleetETag only when the returned JSON has version > 0. Pinned
        # migration 003 tables asset_maintenance_periods and
        # vehicle_charger_pairings have no version column, so no ETag exists.
        unversioned = {
            "POST /api/v1/admin/vehicles/{vehicle_id}/maintenance",
            "POST /api/v1/admin/chargers/{charger_id}/maintenance",
            "POST /api/v1/admin/pairings",
            "POST /api/v1/admin/pairings/{pairing_id}/end",
        }
        if operation in unversioned:
            corrected = copy.deepcopy(response)
            headers = corrected.get("headers", {})
            if headers.get("ETag") != {"type": "string"}:
                raise ValueError("Pinned Go fleet ETag schema changed unexpectedly")
            headers.pop("ETag")
            return corrected
        return response
    key, value = item
    corrected = copy.deepcopy(response)
    corrected["content"] = {
        "application/json": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {key: {"const": value, "type": "string"}},
                    "required": [key],
                    "additionalProperties": False,
                }
            },
            "required": ["data"],
            "additionalProperties": False,
        }
    }
    return corrected


def _schema_refines(go: dict[str, Any], python: dict[str, Any], depth: int = 0) -> bool:
    """Conservative JSON Schema subset proof, not representation equality.

    Accept only where every value described by the *Python* success DTO is
    also permitted by the pinned Go response schema. Unknown constraints or
    unresolved composition fail closed. This is not a runtime behavior test.
    """
    if depth > 40 or not isinstance(go, dict) or not isinstance(python, dict):
        return False
    if any(k in go or k in python for k in ("$ref", "allOf", "oneOf", "not", "if", "then", "else", "dependentSchemas")):
        return False
    if "anyOf" in python:
        return all(_schema_refines(go, part, depth + 1) for part in python["anyOf"])
    if "anyOf" in go:
        return any(_schema_refines(part, python, depth + 1) for part in go["anyOf"])
    known = {"type", "properties", "required", "additionalProperties", "items", "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "format", "enum", "const", "pattern"}
    if set(go) - known:
        return False
    gt, pt = go.get("type"), python.get("type")
    if gt is not None:
        if pt is None:
            return False
        accepted = set(gt if isinstance(gt, list) else [gt])
        offered = set(pt if isinstance(pt, list) else [pt])
        if "number" in accepted:
            accepted.add("integer")
        if not offered <= accepted:
            return False
    if "const" in go and python.get("const") != go["const"]:
        return False
    if "enum" in go:
        offered_enum = python.get("enum")
        if offered_enum is None and "const" in python:
            offered_enum = [python["const"]]
        if offered_enum is None or not set(offered_enum) <= set(go["enum"]):
            return False
    if "format" in go and go["format"] != python.get("format"):
        return False
    if "pattern" in go and go["pattern"] != python.get("pattern"):
        return False
    for low in ("minItems", "minLength", "minimum"):
        if low in go and (low not in python or python[low] < go[low]):
            return False
    for high in ("maxItems", "maxLength", "maximum"):
        if high in go and (high not in python or python[high] > go[high]):
            return False
    if gt == "object":
        go_props, py_props = go.get("properties", {}), python.get("properties", {})
        if not set(go.get("required", [])) <= set(python.get("required", [])):
            return False
        ga, pa = go.get("additionalProperties", True), python.get("additionalProperties", True)
        if ga is False and (pa is not False or not set(py_props) <= set(go_props)):
            return False
        for name, gprop in go_props.items():
            if name not in py_props:
                if name in go.get("required", []) or pa is not False:
                    return False
            elif not _schema_refines(gprop, py_props[name], depth + 1):
                return False
        # A restricted Go extension schema must also be honored by all
        # Python-defined extras, even if optional.
        if isinstance(ga, dict):
            for name,p in py_props.items():
                if name not in go_props and not _schema_refines(ga,p,depth + 1):
                    return False
            if isinstance(pa, dict) and not _schema_refines(ga,pa,depth + 1):
                return False
            if pa is True:
                return False
    if gt == "array":
        if "items" in go:
            if "items" not in python or not _schema_refines(go["items"], python["items"], depth + 1):
                return False
    return True


def _success_response_refines(go: dict[str, Any], python: dict[str, Any]) -> bool:
    """Prove declared Python success payload is accepted by Go's schema."""
    if not isinstance(go, dict) or not isinstance(python, dict):
        return False
    gc,pc = go.get("content", {}), python.get("content", {})
    if not gc or any(media not in pc or not _schema_refines(gs, pc[media]) for media,gs in gc.items()):
        return False
    gh, ph = go.get("headers", {}), python.get("headers", {})
    return all(name in ph and _schema_refines(schema, ph[name]) for name,schema in gh.items())

def _response_schemas(
    operation: dict[str, Any], document: dict[str, Any], *, mandatory_headers: dict[str, Any] | None = None
) -> dict[str, Any]:
    definitions = document.get("components", {}).get("schemas", {})
    response_refs = document.get("components", {}).get("responses", {})
    header_refs = document.get("components", {}).get("headers", {})
    result = {}
    for status, entry in sorted(operation.get("responses", {}).items()):
        if "$ref" in entry:
            entry = response_refs.get(entry["$ref"].rsplit("/", 1)[-1], {})
        headers = {
            name: _canonical_schema(
                _resolve_header(value, header_refs), definitions
            )
            for name, value in sorted(entry.get("headers", {}).items())
        }
        for name, value in (mandatory_headers or {}).items():
            headers.setdefault(name, _canonical_schema(value, definitions))
        result[str(status)] = {
            "content": {
                media: _canonical_schema(content.get("schema"), definitions)
                for media, content in sorted(entry.get("content", {}).items())
            },
            "headers": headers,
        }
    return result


def _resolve_header(value: Any, header_refs: dict[str, Any]) -> Any:
    """Resolve reusable OpenAPI Header Objects before comparing schemas."""
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if reference and reference.startswith("#/components/headers/"):
        resolved = copy.deepcopy(header_refs.get(reference.rsplit("/", 1)[-1], {}))
        resolved.update({key: item for key, item in value.items() if key != "$ref"})
        value = resolved
    return value.get("schema", value)


def _parameters(path_item: dict[str, Any], operation: dict[str, Any], document: dict[str, Any]):
    definitions = document.get("components", {}).get("schemas", {})
    parameter_refs = document.get("components", {}).get("parameters", {})
    values = {}
    for parameter in [*path_item.get("parameters", []), *operation.get("parameters", [])]:
        if "$ref" in parameter and parameter["$ref"].startswith("#/components/parameters/"):
            parameter = copy.deepcopy(parameter_refs.get(parameter["$ref"].rsplit("/", 1)[-1], {}))
        elif "$ref" in parameter:
            values[("$ref", parameter["$ref"])] = {"$ref": parameter["$ref"]}
            continue
        key = (parameter.get("in"), parameter.get("name"))
        required = bool(parameter.get("required"))
        raw_parameter_schema = parameter.get("schema")
        # A non-required query parameter is absent when it has no value. Query
        # serialization has no JSON null token, so Ninja's ``T | None`` model
        # is wire-equivalent to the published optional ``T`` parameter only
        # for this exact scalar-null representation. Do not apply this to
        # headers, request bodies, required parameters, or compositions.
        if parameter.get("in") == "query" and not required and isinstance(raw_parameter_schema, dict):
            members = raw_parameter_schema.get("anyOf")
            if isinstance(members, list) and len(members) == 2:
                null_members = [member for member in members if member == {"type": "null"}]
                value_members = [member for member in members if member != {"type": "null"}]
                if len(null_members) == 1 and len(value_members) == 1 and isinstance(value_members[0], dict):
                    raw_parameter_schema = value_members[0]
        parameter_schema = _canonical_schema(raw_parameter_schema, definitions)
        values[key] = {
            "in": parameter.get("in"),
            "name": parameter.get("name"),
            "required": required,
            "schema": parameter_schema,
        }
    return {f"{key[0]}:{key[1]}": value for key, value in sorted(values.items())}


def _security(operation: dict[str, Any], document: dict[str, Any]) -> Any:
    """Compare security semantics rather than arbitrary scheme names.

    A Django Ninja security class name is an implementation detail.  A Go
    ``BearerAuth`` declaration and a Django ``PlaneBearer`` declaration are
    equivalent only when their resolved HTTP security scheme and scopes agree.
    The absence-vs-empty distinction remains important: ``[]`` is explicitly
    public while an omitted value inherits document-level security.
    """
    # With no document-level requirement, OpenAPI treats an omitted operation
    # security field as public, exactly like an explicit empty list.
    inherited = document.get("security", [])
    requirements = operation.get("security", inherited)
    if not isinstance(requirements, list):
        return requirements
    schemes = document.get("components", {}).get("securitySchemes", {})
    alternatives = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            alternatives.append(requirement)
            continue
        resolved = []
        for name, scopes in requirement.items():
            scheme = schemes.get(name)
            if scheme is None:
                resolved.append({"missing_scheme": name, "scopes": scopes})
                continue
            resolved.append({
                "scheme": {
                    key: scheme[key]
                    for key in ("type", "scheme", "bearerFormat", "openIdConnectUrl")
                    if key in scheme
                },
                "scopes": sorted(scopes),
            })
        alternatives.append(sorted(resolved, key=lambda value: json.dumps(value, sort_keys=True)))
    return sorted(alternatives, key=lambda value: json.dumps(value, sort_keys=True))


def compare_documents(
    go: dict[str, Any], python: dict[str, Any], *, native_diagnostics=(), pinned_go_runtime: bool = False
) -> dict[str, Any]:
    """Pure comparison used by both the CLI and controlled mismatch tests."""
    go_schema = go.get("components", {}).get("schemas", {})
    py_schema = python.get("components", {}).get("schemas", {})
    go_ops = {(path, method): operation for path, item in go["paths"].items()
              for method, operation in item.items() if method in METHODS}
    py_ops = {(path, method): operation for path, item in python.get("paths", {}).items()
              for method, operation in item.items() if method in METHODS}
    discrepancies = []
    for (path, method), go_op in sorted(go_ops.items()):
        label = f"{method.upper()} {path}"
        py_op = py_ops.get((path, method))
        if py_op is None:
            if (path, method) in native_diagnostics:
                discrepancies.append({"operation": label, "type": "native_operation_undeclared"})
            else:
                discrepancies.append({"operation": label, "type": "missing_operation"})
            continue
        go_path_item, py_path_item = go["paths"][path], python["paths"][path]
        if bool(go_op.get("requestBody", {}).get("required")) != bool(
            py_op.get("requestBody", {}).get("required")
        ):
            discrepancies.append({"operation": label, "type": "request_body_required_mismatch"})
        go_body, py_body = _body_schema(go_op, go_schema), _body_schema(py_op, py_schema)
        if pinned_go_runtime:
            go_body = _pinned_go_request_schema(label, go_body)
        if go_body != py_body:
            discrepancies.append({"operation": label, "type": "request_schema_mismatch", "go": go_body,
                                  "python": py_body})
        go_parameters = _parameters(go_path_item, go_op, go)
        py_parameters = _parameters(py_path_item, py_op, python)
        if pinned_go_runtime and label == "GET /api/v1/admin/pricing/policies":
            # pricing.go accepts omitted/empty `status` as the all-status filter.
            # The frozen YAML enumerated only the three nonempty statuses.
            go_status = go_parameters.get("query:status", {}).get("schema", {})
            if go_status.get("enum") == ["DRAFT", "PUBLISHED", "WITHDRAWN"]:
                go_status["enum"] = ["", "DRAFT", "PUBLISHED", "WITHDRAWN"]
        if go_parameters != py_parameters:
            discrepancies.append({"operation": label, "type": "parameter_mismatch", "go": go_parameters,
                                  "python": py_parameters})
        go_security, py_security = _security(go_op, go), _security(py_op, python)
        if go_security != py_security:
            discrepancies.append({"operation": label, "type": "security_declaration_mismatch",
                                  "go": go_security, "python": py_security})
        # Pinned Server.Handler applies X-Request-ID to every API response.
        # Some published component responses omit the header declaration, so
        # source-backed comparison adds the effective header only when the
        # caller explicitly identifies this document as the pinned Go runtime.
        go_headers = (
            {"X-Request-ID": {"type": "string", "format": "uuid"}}
            if pinned_go_runtime
            else None
        )
        go_responses = _response_schemas(go_op, go, mandatory_headers=go_headers)
        py_responses = _response_schemas(py_op, python)
        for status, go_response in go_responses.items():
            if pinned_go_runtime and status.startswith("2"):
                go_response = _pinned_go_success_response(label, go_response)
            py_response = py_responses.get(status)
            if go_response == py_response:
                continue
            if pinned_go_runtime and status.startswith("2") and _success_response_refines(go_response, py_response):
                continue
            kind = "success_response_schema_mismatch" if status.startswith("2") else "error_response_mismatch"
            discrepancies.append({"operation": label, "type": kind, "status": status,
                                  "go": go_response, "python": py_response})
    return {
        "go_operations": len(go_ops),
        "python_operation_count": len(py_ops),
        "go_operations_found_in_python": len(go_ops.keys() & (py_ops.keys() | set(native_diagnostics))),
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
    }


def _django_openapi() -> tuple[dict[str, Any], set[tuple[str, str]]]:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.urls import Resolver404, resolve

    from cow.api import api
    from cow.openapi import add_diagnostics

    native = set()
    for path in ("/health/live", "/health/ready", "/version", "/openapi.yaml"):
        try:
            resolve(path)
            native.add((path, "get"))
        except Resolver404:
            pass
    return json.loads(json.dumps(add_diagnostics(api.get_openapi_schema()))), native


def compare() -> dict[str, Any]:
    from cow.go_wire import SPEC_PATH

    go = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    python, native = _django_openapi()
    report = compare_documents(go, python, native_diagnostics=native, pinned_go_runtime=True)
    if report["go_operations"] != 82:
        raise RuntimeError("frozen Go API operation catalog changed")
    report.update({
        "checker_version": CHECKER_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "go_revision": "1e966a2ce29bc2d1ef3e25500ee474c1ab380853",
        "published_go_openapi_revision": "070cd06514b4d63b29aaa12ff115d08c9551d0c8",
        "python_base_revision": "760c64f17c3ecd196f95f6e344360ad22dc8c3a6",
        "native_diagnostics": sorted(f"{method.upper()} {path}" for path, method in native),
        "caveat": "Static declarations only; handler behavior, database state, SMTP, and live acceptance remain separate gates.",
    })
    return report


def _csv_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _finding_key(finding: dict[str, Any]) -> tuple[str, str, str]:
    return finding["operation"], finding["type"], str(finding.get("status", ""))


def _current_classification(finding: dict[str, Any]) -> tuple[str, str]:
    """Give every current difference a traceable, conservative disposition."""
    kind, operation = finding["type"], finding["operation"]
    if kind == "error_response_mismatch":
        return (
            "Python declaration/schema deficiency",
            "The generated operation omits this declared error response; runtime status behavior remains unverified.",
        )
    if kind == "success_response_schema_mismatch":
        return (
            "Python declaration/schema deficiency",
            "The generated success response is missing or structurally different; inspect handler serialization before closure.",
        )
    if kind == "parameter_mismatch":
        return (
            "Header/parameter declaration discrepancy",
            "Compare raw Django header/query processing with the pinned Go handler before declaring equivalence.",
        )
    if kind == "request_schema_mismatch" and "/auth/logout" in operation:
        return (
            "Stale Go specification",
            "Pinned Go post() checks only Content-Type and deliberately does not decode logout bodies; the YAML's generic object body is descriptive, not a handler constraint.",
        )
    if kind == "request_schema_mismatch" and (
        operation == "POST /api/v1/auth/verify-email" or operation.endswith("/auth/refresh")
    ):
        return (
            "Stale Go specification",
            "Pinned Go source enforces 43-byte token length but no alphabet pattern; current Django follows source over YAML.",
        )
    if kind == "request_schema_mismatch" and operation == "POST /api/v1/auth/reset-password":
        return (
            "Stale Go specification",
            "Pinned Go source checks token length before digest lookup; current Django follows that behavior over YAML pattern text.",
        )
    if kind == "request_schema_mismatch" and any(
        segment in operation for segment in ("/vehicles", "/chargers")
    ):
        return (
            "Stale Go specification or Python declaration discrepancy",
            "Pinned fleet.ValidateFields supports nullable catalog fields; compare each property before choosing the source correction.",
        )
    if kind == "request_schema_mismatch" and "pricing/policies" in operation:
        return (
            "Python declaration/schema deficiency",
            "Runtime validates Draft strictly, but the HTTP-facing generic dict declaration loses nested constraints.",
        )
    if kind == "native_operation_undeclared":
        return (
            "Stale Go specification or Python declaration discrepancy",
            "Diagnostics are URLconf routes and require source-based response reconciliation rather than a route-name waiver.",
        )
    return "Unresolved", "Requires per-operation source and isolated behavior evidence."


def write_resolution_ledgers(output: Path, report: dict[str, Any]) -> None:
    """Reconcile immutable V2 findings and preserve report-specific ledgers."""
    historical = json.loads(HISTORICAL_REPORT.read_text(encoding="utf-8"))
    current = report["discrepancies"]
    current_keys = {_finding_key(finding): index for index, finding in enumerate(current, start=1)}
    rows = []
    for index, finding in enumerate(historical["discrepancies"], start=1):
        key = _finding_key(finding)
        current_id = current_keys.get(key)
        if current_id is None:
            classification = "Statically reconciled; runtime verification pending"
            correction = "Current checker no longer reports this exact operation/type/status difference."
            disposition = "PARTIALLY_RESOLVED_STATIC"
            result = "STATIC_RECONCILED; dynamic behavior unverified"
        else:
            classification, correction = _current_classification(current[current_id - 1])
            disposition = "UNCHANGED_OR_RECLASSIFIED"
            result = "UNVERIFIED"
        rows.append({
            "historical_id": index,
            "current_finding_id": current_id or "",
            "operation": finding["operation"],
            "finding_type": finding["type"],
            "status": finding.get("status", ""),
            "classification": classification,
            "go_declaration": _csv_json(finding.get("go")),
            "python_declaration": _csv_json(finding.get("python")),
            "go_handler_evidence": "Pinned Go source is authoritative; static reconciliation does not prove handler behavior.",
            "python_handler_evidence": "Current generated OpenAPI compared; runtime behavior requires isolated verification.",
            "required_correction": correction,
            "test_result": result,
            "final_disposition": disposition,
        })
    fields = ["current_finding_id", "operation", "type", "status", "classification", "evidence", "go", "python"]
    current_rows = []
    for index, finding in enumerate(current, start=1):
        classification, evidence = _current_classification(finding)
        current_rows.append({
            "current_finding_id": index,
            "operation": finding["operation"],
            "type": finding["type"],
            "status": finding.get("status", ""),
            "classification": classification,
            "evidence": evidence,
            "go": _csv_json(finding.get("go")),
            "python": _csv_json(finding.get("python")),
        })

    def write_csv(path: Path, fieldnames: list[str], values: list[dict[str, Any]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(values)

    # Preserve immutable, report-specific artifacts first. The legacy generic
    # filenames remain a convenience pointer to the latest run only.
    write_csv(output.parent / f"{output.stem}_RESOLUTION_LEDGER.csv", list(rows[0]), rows)
    write_csv(output.parent / f"{output.stem}_CURRENT_DISCREPANCY_LEDGER.csv", fields, current_rows)
    write_csv(output.parent / "RESOLUTION_LEDGER.csv", list(rows[0]), rows)
    write_csv(output.parent / "CURRENT_DISCREPANCY_LEDGER.csv", fields, current_rows)

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in current_rows:
        grouped.setdefault((row["classification"], row["type"], row["status"]), []).append(row)
    root_cause_map = {
        "report": output.name,
        "checker_version": report["checker_version"],
        "discrepancy_count": report["discrepancy_count"],
        "groups": [
            {
                "classification": classification,
                "finding_type": finding_type,
                "status": status or None,
                "count": len(values),
                "finding_ids": [int(value["current_finding_id"]) for value in values],
                "operations": sorted({value["operation"] for value in values}),
            }
            for (classification, finding_type, status), values in sorted(grouped.items())
        ],
    }
    (output.parent / f"{output.stem}_ROOT_CAUSE_MAP.json").write_text(
        json.dumps(root_cause_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = compare()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_resolution_ledgers(args.output, report)
    print(f"CHECKER_VERSION={CHECKER_VERSION}")
    print(f"GO_OPERATIONS={report['go_operations']}")
    print(f"PYTHON_ROUTE_MATCHES={report['go_operations_found_in_python']}")
    print(f"SCHEMA_DISCREPANCIES={report['discrepancy_count']}")
    print(f"REPORT={args.output}")
    if report["discrepancy_count"]:
        print("GO_STATIC_CONTRACT_GATE=FAIL (differences retained for review)")
        return 1
    print("GO_STATIC_CONTRACT_GATE=PASS; DYNAMIC_BEHAVIOR_NOT_PROVEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
