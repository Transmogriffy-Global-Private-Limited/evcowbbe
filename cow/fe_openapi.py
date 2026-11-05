"""Frontend-facing descriptions layered on top of the actual Django Ninja schema.

Only annotate an existing operation. Never synthesize business routes, override
security, request/response schemas, status sets, or existing operation IDs.
"""

from __future__ import annotations

import json
from copy import deepcopy

INTRO = """## Frontend integration guide (development API)

**This is the live Django development contract, not proof that all business flows
or physical charger operations are commissioned.** APIs not present below are
not implemented by this service. Avoid generating clients from the archived Go
contract without comparing behavior.

### Start here

1. Select the correct **Customer**, **Driver**, or **Admin** authentication plane.
   Login at `/api/v1/{plane}/auth/login`. A successful login can return either
   an opaque bearer token pair or a two-factor challenge; if challenged, submit
   `/api/v1/{plane}/auth/verify-otp` before proceeding.
2. In Swagger, click **Authorize** for the security scheme that corresponds to
   the endpoint. Send `Authorization: Bearer <opaque-access-token>`. Credentials
   are plane-specific; admin rights are also gated by server-side capabilities.
3. Follow the *request body and response schemas for each operation*. Normal
   success envelopes use `data`; failures use `error` with a machine-readable
   `code`, a message, `request_id`, and `details`. Never use message text as a
   control-flow key.
4. Preserve `X-Request-ID` for diagnostics. Where required, generate a unique
   `Idempotency-Key` per **logical mutation**, and reuse it only when retrying
   that same request. For versioned changes, obtain a current ETag/version and
   send the exact quoted `If-Match` value. Handle `409`, `412`, and `428`
   explicitly rather than blindly retrying.
5. Dates/times should carry their UTC offset. Monetary fields ending in
   `_paise` are **integer INR minor units**. Charging commands returning HTTP
   `202` are only accepted/queued; they do **not** prove physical energy flow.

### Integration boundaries

- OpenAPI JSON: `/api/openapi.json`; YAML: `/openapi.yaml` (same documented schema).
- The FE and the backend must agree on CORS origins before cross-origin browser
  calls work; Swagger on the API origin is not proof an external FE origin is
  allowed.
- Customer-saved EVs/addresses are customer-submitted data, **not verified**
  charger compatibility or verified geographical serviceability.
- HAL fact ingestion is signed server-to-server traffic, not a browser API.
- Pydantic and domain validators can enforce conditions beyond JSON Schema
  (such as nonempty patches, cross-field EV charging-mode consistency and
  country-code constraints). FE clients must handle HTTP 422 even when a JSON
  example looks structurally correct.
- Several legacy or newly introduced endpoints still have **generic success
  response schemas**. Those are visibly marked below. Do not assume unstated
  response fields; require a source-backed typed contract and integration test.

See `docs/FRONTEND_INTEGRATION.md` in the repository for the implementation
sequence, auth/error patterns, and documentation-quality gates.
"""

TAG_DESCRIPTIONS = {
    "01 | Health & version": "Public health and logical API version probes; readiness checks PostgreSQL, not charger operations.",
    "02 | Shared account recovery": "Verification, resend and password reset endpoints shared across login planes. No bearer token is required on these declared routes.",
    "03 | Customer identity": "Customer registration, login, opaque sessions, refresh, profile identity, password and two-factor verification.",
    "04 | Driver identity": "Driver identity/session flows; a driver token cannot authenticate as a customer or admin.",
    "05 | Admin identity": "Admin identity/session flows. Authentication does not imply every RBAC capability.",
    "06 | Customer profile": "Customer-editable personal profile and versioned updates.",
    "07 | Customer EVs": "Customer-saved EV records. Capability evidence is unverified customer data.",
    "08 | Customer addresses": "Saved and temporary addresses; default selection is version-controlled.",
    "09 | Customer service requests": "Customer requests, quotes and acceptance; partial development workflow.",
    "10 | Customer jobs": "Customer-owned service job reads; charging/dispatch workflow remains under development.",
    "11 | Admin jobs & charging": "Admin dispatch/job reads and HAL command requests; accepted is not physically started.",
    "12 | Admin fleet vehicles": "Admin fleet vehicles, history, maintenance, availability, and versioned mutations.",
    "13 | Admin fleet chargers": "Admin charger inventory, maintenance, availability, and versioned mutations.",
    "14 | Admin charger connectors": "Admin connector inventory and configuration for registered chargers.",
    "15 | Admin vehicle-charger pairings": "Assignment/pairing lifecycle, subject to compatibility and concurrency guards.",
    "16 | Admin RBAC": "Admin capabilities, role grants/revokes and explicit per-account overrides. All authorization is checked server-side.",
    "17 | Admin pricing policies": "Versioned Admin tariff policy draft, publication, withdrawal and history. Not proof of complete billing/GST/payment orchestration.",
    "18 | HAL internal ingress": "HMAC-signed backend-to-backend facts; not for frontend direct invocation, and not physical acceptance evidence by itself.",
    "19 | Other development endpoints": "Present in the running implementation; review source and actual typed schemas before depending on undeclared behavior.",
}

PLANE = {"app": "Customer", "driver": "Driver", "admin": "Admin"}

KNOWN_SUMMARIES = {
    "health_live": "Check process liveness",
    "health_ready": "Check PostgreSQL readiness",
    "version": "Read logical API version",
    "adminFleetVehiclesList": "List fleet vehicles",
    "adminFleetVehiclesCreate": "Register fleet vehicle",
    "adminFleetVehicleGet": "Get fleet vehicle",
    "adminFleetVehiclePatch": "Update fleet vehicle",
    "adminFleetVehicleHistory": "View fleet vehicle history",
    "adminFleetVehicleAvailability": "Check fleet vehicle availability",
    "adminFleetVehicleDeactivate": "Deactivate fleet vehicle",
    "adminFleetVehicleReactivate": "Reactivate fleet vehicle",
    "adminFleetVehicleMaintenance": "Schedule vehicle maintenance",
    "adminFleetChargersList": "List fleet chargers",
    "adminFleetChargersCreate": "Register fleet charger",
    "adminFleetChargerGet": "Get fleet charger",
    "adminFleetChargerPatch": "Update fleet charger",
    "adminFleetChargerHistory": "View fleet charger history",
    "adminFleetChargerAvailability": "Check fleet charger availability",
    "adminFleetChargerDeactivate": "Deactivate fleet charger",
    "adminFleetChargerReactivate": "Reactivate fleet charger",
    "adminFleetChargerMaintenance": "Schedule charger maintenance",
    "adminFleetConnectorsList": "List charger connectors",
    "adminFleetConnectorCreate": "Add charger connector",
    "adminFleetConnectorPatch": "Update charger connector",
    "adminFleetPairingsList": "List fleet pairings",
    "adminFleetPairCreate": "Pair vehicle and charger",
    "adminFleetPairEnd": "End vehicle-charger pairing",
    "adminPricingList": "List tariff policies",
    "adminPricingCreateDraft": "Create pricing draft",
    "adminPricingGet": "Get pricing policy",
    "adminPricingHistory": "Read pricing policy history",
    "adminPricingPublish": "Publish pricing policy",
    "adminPricingWithdraw": "Withdraw pricing policy",
}

AUTH_ACTIONS = {
    "auth/register": "Register account",
    "auth/login": "Sign in",
    "auth/refresh": "Refresh session",
    "auth/logout": "End current session",
    "auth/logout-all": "Revoke all sessions",
    "auth/password/change": "Change password",
    "auth/verify-otp": "Complete two-factor sign-in",
    "auth/2fa/resend": "Resend two-factor code",
    "auth/2fa/enable/begin": "Begin two-factor activation",
    "auth/2fa/enable/confirm": "Confirm two-factor activation",
    "auth/2fa/disable/begin": "Begin two-factor deactivation",
    "auth/2fa/disable/confirm": "Confirm two-factor deactivation",
    "me": "Get current principal",
}

SHARED_ACTIONS = {
    "verify-email": "Verify email address",
    "resend-verification": "Resend email verification",
    "forgot-password": "Request password reset",
    "reset-password": "Reset password",
}

# Only add examples whose request keys are explicitly verified against cow/api.py.
REQUEST_EXAMPLES = {
    "auth/login": {"email": "person@example.com", "password": "replace-with-your-password"},
    "auth/refresh": {"refresh_token": "r" * 43},
    "auth/verify-otp": {
        "challenge_id": "00000000-0000-4000-8000-000000000001",
        "code": "123456",
    },
    "auth/2fa/resend": {"challenge_id": "00000000-0000-4000-8000-000000000001"},
}


def _tag(path: str) -> str:
    if path in {"/health/live", "/health/ready", "/version"}:
        return "01 | Health & version"
    if path.startswith("/api/v1/auth/"):
        return "02 | Shared account recovery"
    for plane, label in (("app", "03 | Customer identity"),
                         ("driver", "04 | Driver identity"),
                         ("admin", "05 | Admin identity")):
        p = f"/api/v1/{plane}/"
        if path.startswith(p) and path[len(p):] in AUTH_ACTIONS:
            return label
    if path.startswith("/api/v1/app/profile"):
        return "06 | Customer profile"
    if path.startswith("/api/v1/app/vehicles"):
        return "07 | Customer EVs"
    if path.startswith("/api/v1/app/addresses"):
        return "08 | Customer addresses"
    if path.startswith("/api/v1/app/service-requests") or path.startswith("/api/v1/app/quotes"):
        return "09 | Customer service requests"
    if path.startswith("/api/v1/app/jobs"):
        return "10 | Customer jobs"
    if path.startswith("/api/v1/admin/jobs"):
        return "11 | Admin jobs & charging"
    if path.startswith("/api/v1/admin/vehicles"):
        return "12 | Admin fleet vehicles"
    if path.startswith("/api/v1/admin/chargers"):
        if "/connectors" in path:
            return "14 | Admin charger connectors"
        return "13 | Admin fleet chargers"
    if path.startswith("/api/v1/admin/pairings"):
        return "15 | Admin vehicle-charger pairings"
    if path.startswith("/api/v1/admin/rbac"):
        return "16 | Admin RBAC"
    if path.startswith("/api/v1/admin/pricing/policies"):
        return "17 | Admin pricing policies"
    if path.startswith("/api/v1/internal/hal/"):
        return "18 | HAL internal ingress"
    return "19 | Other development endpoints"


def _summary(path: str, method: str, op: dict) -> str:
    if op.get("operationId") in KNOWN_SUMMARIES:
        return KNOWN_SUMMARIES[op["operationId"]]
    segments = path.strip("/").split("/")
    if len(segments) >= 4 and segments[:2] == ["api", "v1"]:
        if segments[2] in PLANE:
            suffix = "/".join(segments[3:])
            if suffix in AUTH_ACTIONS:
                return f"{AUTH_ACTIONS[suffix]} ({PLANE[segments[2]].lower()})"
        if segments[2] == "auth" and segments[3] in SHARED_ACTIONS:
            return SHARED_ACTIONS[segments[3]]
    suffix = segments[-1]
    if suffix.startswith("{") and len(segments) > 1:
        suffix = segments[-2].rstrip("s")
    noun = suffix.replace("-", " ").replace("_", " ")
    verb = {"get": "Read", "post": "Submit", "put": "Replace", "patch": "Update",
            "delete": "Remove", "head": "Inspect"}.get(method, method.upper())
    return f"{verb} {noun}".strip().capitalize()


def _notes(path: str, method: str) -> list[str]:
    notes: list[str] = []
    if path.startswith("/api/v1/") and not path.startswith("/api/v1/internal/hal/"):
        if path.startswith("/api/v1/auth/"):
            notes.append("**Access:** Public credential-recovery or verification flow; rate limits apply.")
        else:
            plane = path.split("/")[3]
            notes.append(
                "**Access:** " + PLANE.get(plane, "Plane-specific")
                + " bearer token where required by the Security section below."
            )
    if path.endswith("/auth/login"):
        notes.append("**Login:** The response may contain tokens or a two-factor challenge. Do not assume tokens are present until verification completes.")
    if path.endswith("/auth/refresh"):
        notes.append("**Refresh:** Use the refresh credential, not the access credential. Treat reuse and revocation errors as sign-out events.")
    if path.endswith("/me"):
        notes.append("**Principal:** Tokens are not interchangeable between the customer, driver and admin planes.")
    if "/rbac/" in path:
        notes.append("**Permissions:** Authorizations and role changes are enforced server-side; a visible FE control never grants permission.")
    if "/pricing/policies" in path:
        notes.append("**Commercial boundary:** Admin policy revisions are versioned. This is not a quote, captured payment, tax invoice or charging settlement.")
    if "/v1/admin/vehicles" in path or "/v1/admin/chargers" in path or "/v1/admin/pairings" in path:
        notes.append("**Fleet:** Use the resource's current ETag and a unique idempotency key for mutations where declared. Historical and availability views are separate from physical charger state.")
    if path.endswith("/history"):
        notes.append("**Audit:** This view may require an additional `audit.read` capability.")
    if "/v1/app/vehicles" in path:
        notes.append("**Evidence:** Saved customer EV details are unverified customer-supplied data, not compatibility approval.")
    if "/v1/app/addresses" in path:
        notes.append("**Addresses:** A default address is subject to ownership, save status, and version concurrency checks. Coordinate fields are decimal strings when returned.")
    if path.startswith("/api/v1/app/") and path.endswith(("/vehicles", "/addresses")) and method == "get":
        notes.append("**List:** This endpoint returns at most 100 saved active records; it does not declare cursor pagination.")
    if path.startswith("/api/v1/app/") and method in {"patch", "delete"}:
        notes.append("**Concurrency:** Fetch the current version (or ETag for a detail resource); use a quoted numeric `If-Match`. A stale version returns HTTP 412.")
    if path.endswith("/make-default"):
        notes.append("**Concurrency:** Requires a current quoted `If-Match` and an idempotency key; read the address before changing its default state.")
    if path.startswith("/api/v1/app/service-requests") or "/api/v1/app/quotes" in path:
        notes.append("**Business state:** The underlying request/quote flow is development-stage and does not itself trigger a charger or payment.")
    if path.startswith("/api/v1/admin/jobs") and path.endswith("/charging/start"):
        notes.append("**Physical safety:** HTTP 202 means a HAL command was accepted for processing, **not** that a charger started or delivered energy. Confirm later signed physical facts/state before showing charging as active.")
    if path.startswith("/api/v1/internal/hal/"):
        notes.append("**Internal only:** Signed, timestamped, authenticated HAL facts are accepted server-to-server. Never expose the shared signing secret or call this route directly from the browser.")
    if path in {"/health/live", "/health/ready"}:
        notes.append("**Diagnostic scope:** Readiness is an API/PostgreSQL signal, not end-to-end charging or SMTP delivery verification.")
    return notes


def _add_customer_headers(path: str, method: str, operation: dict) -> None:
    """Only annotate requirements verified in cow/users/master.py."""
    if not path.startswith("/api/v1/app/"):
        return
    family = path.split("/")[4] if len(path.split("/")) > 4 else ""
    if family not in {"profile", "vehicles", "addresses"}:
        return
    is_create = method == "post" and path in {"/api/v1/app/vehicles", "/api/v1/app/addresses"}
    is_versioned = (method in {"patch", "delete"} or path.endswith("/make-default"))
    if not is_create and not is_versioned:
        return
    params = operation.setdefault("parameters", [])
    seen = {(p.get("in"), p.get("name")) for p in params if isinstance(p, dict)}
    if is_versioned and ("header", "If-Match") not in seen:
        params.append({
            "in": "header", "name": "If-Match", "required": True,
            "description": "Current quoted decimal resource version, e.g. `\"1\"`; profile version 0 is allowed on first edit.",
            "schema": {"type": "string", "pattern": '^"(0|[1-9][0-9]*)"$'},
            "example": '"0"' if family == "profile" else '"1"',
        })
    if ("header", "Idempotency-Key") not in seen:
        params.append({
            "in": "header", "name": "Idempotency-Key", "required": bool(is_versioned),
            "description": "16-128 visible ASCII characters (excluding comma). Reuse only for a replay of the same logical mutation.",
            "schema": {"type": "string", "minLength": 16, "maxLength": 128},
            "example": "frontend-logical-request-0001",
        })


def _add_customer_etag(path: str, method: str, operation: dict) -> None:
    if not path.startswith("/api/v1/app/"):
        return
    if not (path.startswith("/api/v1/app/vehicles") or
            path.startswith("/api/v1/app/addresses") or
            (path == "/api/v1/app/profile" and method == "patch")):
        return
    if method == "get" and path in {"/api/v1/app/vehicles", "/api/v1/app/addresses"}:
        return
    for status, response in operation.get("responses", {}).items():
        if str(status).startswith("2") and isinstance(response, dict):
            response.setdefault("headers", {}).setdefault("ETag", {
                "description": "Current quoted resource version; pass it back as If-Match for changes.",
                "schema": {"type": "string"},
            })


def _request_example(path: str, operation: dict) -> None:
    segments = path.strip("/").split("/")
    if len(segments) < 5 or segments[:2] != ["api", "v1"]:
        return
    tail = "/".join(segments[3:])
    example = REQUEST_EXAMPLES.get(tail) if segments[2] in PLANE else None
    if example is None:
        return
    content = operation.get("requestBody", {}).get("content", {})
    media = content.get("application/json")
    if isinstance(media, dict) and "examples" not in media and "example" not in media:
        media["examples"] = {
            "frontend_flow": {
                "summary": "Illustrative fields, not usable credentials",
                "value": deepcopy(example),
            }
        }


# Customer master-data handlers validate Pydantic objects internally but currently
# expose raw `dict` to Ninja. Publish those exact input models as documentation
# without introducing a response serializer or changing runtime request handling.
_CUSTOMER_INPUTS = {
    ("patch", "/api/v1/app/profile"): ("ProfilePatch", "FE_CustomerProfilePatch"),
    ("post", "/api/v1/app/vehicles"): ("VehicleCreate", "FE_CustomerVehicleCreate"),
    ("patch", "/api/v1/app/vehicles/{vehicle_id}"): ("VehiclePatch", "FE_CustomerVehiclePatch"),
    ("post", "/api/v1/app/addresses"): ("AddressCreate", "FE_CustomerAddressCreate"),
    ("patch", "/api/v1/app/addresses/{address_id}"): ("AddressPatch", "FE_CustomerAddressPatch"),
}

_CUSTOMER_EXAMPLES = {
    "FE_CustomerProfilePatch": {"full_name": "Example Customer"},
    "FE_CustomerVehicleCreate": {
        "make": "Example Manufacturer", "model": "Sample EV",
        "supports_ac": True, "supports_dc": False,
    },
    "FE_CustomerVehiclePatch": {"label": "Daily commute"},
    "FE_CustomerAddressCreate": {
        "address": "Example Street, Sample City, India",
        "latitude": "22.5726000", "longitude": "88.3639000", "saved": True,
    },
    "FE_CustomerAddressPatch": {"instructions": "Call upon arrival"},
}


def _install_customer_models(document: dict) -> None:
    """Publish 5 request contracts and 5 source-verified response structures."""
    from cow.users import contracts as c

    models = {name: getattr(c, name) for name, _ in _CUSTOMER_INPUTS.values()}
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    for class_name, exposed_name in _CUSTOMER_INPUTS.values():
        raw = models[class_name].model_json_schema(mode="validation")
        if "$defs" in raw or exposed_name in schemas:
            raise ValueError("Cannot safely install FE customer schema: " + exposed_name)
        if class_name.endswith("Patch"):
            # Source validators require nonempty patches but JSON Schema does not
            # auto-translate Pydantic's model_validator after-hook.
            raw["minProperties"] = 1
        schemas[exposed_name] = raw

    def response_properties(model_name: str) -> dict:
        raw = getattr(c, model_name).model_json_schema(mode="serialization")
        if "$defs" in raw:
            raise ValueError("Unexpected nested customer response model")
        return deepcopy(raw["properties"])

    vehicle_props = response_properties("VehicleFields")
    vehicle_props.update({
        "id": {"type": "string", "format": "uuid"},
        "active": {"type": "boolean"},
        "version": {"type": "integer", "minimum": 1},
        "capability_evidence": {"type": "string", "const": "UNVERIFIED_CUSTOMER_DATA"},
    })
    address_props = response_properties("AddressFields")
    for coordinate in ("latitude", "longitude"):
        address_props[coordinate] = {
            "type": "string", "description": "Decimal degrees, formatted with 7 fractional places."
        }
    address_props.update({
        "id": {"type": "string", "format": "uuid"},
        "active": {"type": "boolean"},
        "is_default": {"type": "boolean"},
        "version": {"type": "integer", "minimum": 1},
    })
    models_out = {
        "FE_CustomerProfileData": {
            "full_name": {"type": "string"},
            "phone": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "version": {"type": "integer", "minimum": 0},
        },
        "FE_CustomerVehicleData": vehicle_props,
        "FE_CustomerAddressData": address_props,
    }
    for name, props in models_out.items():
        if name in schemas:
            raise ValueError("Customer response schema collision: " + name)
        schemas[name] = {
            "type": "object", "additionalProperties": False,
            "properties": props, "required": sorted(props),
        }
    for name, data_name, is_list in (
        ("FE_CustomerProfileEnvelope", "FE_CustomerProfileData", False),
        ("FE_CustomerVehicleEnvelope", "FE_CustomerVehicleData", False),
        ("FE_CustomerVehicleListEnvelope", "FE_CustomerVehicleData", True),
        ("FE_CustomerAddressEnvelope", "FE_CustomerAddressData", False),
        ("FE_CustomerAddressListEnvelope", "FE_CustomerAddressData", True),
    ):
        child = {"$ref": "#/components/schemas/" + data_name}
        if is_list:
            child = {"type": "array", "items": child}
        schemas[name] = {
            "type": "object", "additionalProperties": False,
            "properties": {"data": child}, "required": ["data"],
        }


def _enrich_customer_operation(document: dict, path: str, method: str, operation: dict) -> None:
    key = (method, path)
    if key in _CUSTOMER_INPUTS:
        _, name = _CUSTOMER_INPUTS[key]
        body = operation.get("requestBody")
        if not isinstance(body, dict) or "application/json" not in body.get("content", {}):
            raise ValueError("Missing declared customer request body: " + path)
        media = body["content"]["application/json"]
        media["schema"] = {"$ref": "#/components/schemas/" + name}
        media["examples"] = {
            "example": {"summary": "Source-validated sample", "value": _CUSTOMER_EXAMPLES[name]}
        }

    if not path.startswith("/api/v1/app/"):
        return
    tail = path[len("/api/v1/app/"):]
    result_model = None
    if tail == "profile" and method in {"get", "patch"}:
        result_model = "FE_CustomerProfileEnvelope"
    elif tail.startswith("vehicles"):
        result_model = "FE_CustomerVehicleListEnvelope" if tail == "vehicles" and method == "get" else "FE_CustomerVehicleEnvelope"
    elif tail.startswith("addresses"):
        result_model = "FE_CustomerAddressListEnvelope" if tail == "addresses" and method == "get" else "FE_CustomerAddressEnvelope"
    if result_model is None:
        return
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        raise ValueError("Missing customer response map: " + path)
    # Both create handlers explicitly return 201 through _replay(JsonResponse).
    if (method, path) in {
        ("post", "/api/v1/app/vehicles"),
        ("post", "/api/v1/app/addresses"),
    }:
        if "200" not in responses or "201" in responses:
            raise ValueError("Unexpected generated create status: " + path)
        responses["201"] = responses.pop("200")
        responses["201"]["description"] = "Created"
    wanted_status = "201" if method == "post" and tail in {"vehicles", "addresses"} else "200"
    result = responses.get(wanted_status)
    if not isinstance(result, dict):
        raise ValueError("Missing generated success status for " + path)
    media = result.setdefault("content", {}).setdefault("application/json", {})
    media["schema"] = {"$ref": "#/components/schemas/" + result_model}
    if method == "get" and tail in {"vehicles", "addresses"}:
        media["examples"] = {"empty": {"summary": "No saved entries", "value": {"data": []}}}
    if method == "get" and tail == "profile":
        media["examples"] = {
            "new": {
                "summary": "Account without customer profile edits",
                "value": {"data": {"full_name": "", "phone": None, "version": 0}},
            }
        }
    # The existing middleware consistently installs X-Request-ID. The generic
    # customer handlers also return standard error envelopes, so advertise the
    # exact status families established by their source guards.
    error_codes = {"401": "Missing or invalid customer credentials"}
    if method in {"post", "patch"}:
        error_codes.update({"415": "Expected application/json", "422": "Invalid payload"})
    if method in {"patch", "delete"} or tail.endswith("/make-default"):
        error_codes.update({
            "400": "Malformed If-Match or request headers",
            "412": "Resource version mismatch",
            "428": "Missing If-Match or Idempotency-Key",
        })
    if "{" in tail:
        error_codes["404"] = "Customer-owned resource not found"
    for status, description in error_codes.items():
        if status in responses:
            continue
        from cow.common.contracts import GO_ERROR_ENVELOPE_SCHEMA
        responses[status] = {
            "description": description,
            "headers": {
                "X-Request-ID": {
                    "description": "Request correlation UUID.",
                    "schema": {"type": "string", "format": "uuid"},
                }
            },
            "content": {"application/json": {"schema": deepcopy(GO_ERROR_ENVELOPE_SCHEMA)}},
        }


def _is_generic_success(operation: dict) -> bool:
    """Conservative: flag only obvious schema-less/undifferentiated success bodies."""
    for status, response in operation.get("responses", {}).items():
        if not str(status).startswith("2") or not isinstance(response, dict):
            continue
        content = response.get("content", {})
        for media in content.values():
            if not isinstance(media, dict):
                continue
            schema = media.get("schema", {})
            if not isinstance(schema, dict):
                return True
            if not schema or schema == {"type": "object"}:
                return True
            if schema.get("type") == "object" and not schema.get("properties") and not schema.get("$ref"):
                return True
    return False


def decorate_fe_schema(schema: dict) -> dict:
    """Add presentation/FE help, preserving every original operation and contract field."""
    # Ninja's native schema may contain integer HTTP status keys. Normalize
    # first so later status handling, JSON and YAML see the same string keys.
    schema = json.loads(json.dumps(schema))
    if schema.get("x-fe-documentation-version") == "1":
        return schema
    if "x-fe-documentation-version" in schema:
        raise ValueError("Unknown FE documentation schema version")
    info = schema.setdefault("info", {})
    if not isinstance(info, dict):
        raise ValueError("Unexpected OpenAPI info object")
    if "## Frontend integration guide (development API)" not in str(info.get("description", "")):
        info["description"] = (
            str(info.get("description", "")).strip() + "\n\n" + INTRO
        ).strip()
    info["x-documentation-role"] = "development-frontend-integration"
    # Same-origin relative URL works on the public host and in local tests.
    schema["servers"] = [{"url": "/", "description": "Same-origin development API"}]
    used: set[str] = set()
    gap_operations: list[str] = []
    operation_count = 0
    _install_customer_models(schema)
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            raise ValueError(f"Unexpected OpenAPI path entry: {path}")
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "patch", "put", "delete", "head", "options"}:
                continue
            if not isinstance(operation, dict):
                raise ValueError(f"Unexpected OpenAPI operation: {path} {method}")
            operation_count += 1
            tag = _tag(path)
            used.add(tag)
            operation["tags"] = [tag]
            operation["summary"] = _summary(path, method, operation)
            existing = str(operation.get("description", "")).strip()
            notes = _notes(path, method)
            if not notes and not existing:
                notes.append(TAG_DESCRIPTIONS[tag])
            _add_customer_headers(path, method, operation)
            _add_customer_etag(path, method, operation)
            _enrich_customer_operation(schema, path, method, operation)
            _request_example(path, operation)
            incomplete = _is_generic_success(operation)
            operation["x-fe-schema-detail"] = "generic-success" if incomplete else "declared"
            if incomplete:
                gap_operations.append(method.upper() + " " + path)
                notes.append("**Documentation gap:** The success response is not fully typed in generated OpenAPI. Treat the documented HTTP route as implemented, but do not infer specific payload fields from an open-ended object schema. FE integration needs a typed, source-verified response contract.")
            operation["description"] = "\n\n".join(x for x in (existing, *notes) if x)
    schema["tags"] = [
        {"name": name, "description": description}
        for name, description in TAG_DESCRIPTIONS.items() if name in used
    ]
    schema["x-fe-documentation-version"] = "1"
    schema["x-fe-contract-coverage"] = {
        "operations": operation_count,
        "declared_success_shapes": operation_count - len(gap_operations),
        "generic_success_shapes": len(gap_operations),
        "generic_operations": sorted(gap_operations),
    }
    return schema
