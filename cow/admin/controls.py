"""Admin RBAC and legacy pricing tombstones.

Only Zone composite tariffs are a supported Admin tariff mode. Old generic
pricing URLs remain 410 Gone for explicit migration feedback, not as a second
contract in Swagger.
"""

import re
from typing import Literal
from uuid import UUID

from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError
from django.http import JsonResponse
from ninja import Query, Schema
from pydantic import ConfigDict, Field

from cow.common.contracts import AckEnvelope
from cow.api_parity_responses import (PricingEnvelope, PricingHistoryEnvelope, PricingListEnvelope)
from cow.common.errors import reject
from cow.models import Principal
from cow.pricing_contract import Draft
from cow.services import admin_rbac, idempotency, pricing_admin, rbac
from cow.services.pricing_wire import InvalidPricingBody, check_body


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RoleChange(StrictSchema):
    role: Literal[
        "FULL_ADMIN",
        "OPERATIONS",
        "DRIVER_RECRUITMENT",
        "FINANCE_COMMERCIAL",
        "SUPPORT",
        "TECHNICAL_HAL",
        "READ_ONLY",
    ]
    reason: str = Field(min_length=8, max_length=500)


class Reason(StrictSchema):
    reason: str = Field(min_length=8, max_length=500)


class OverrideChange(Reason):
    decision: Literal["ALLOW", "DENY"]


def _json(request):
    if request.content_type.split(";", 1)[0].lower() != "application/json":
        reject(415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > 32768 or len(request.body) > 32768:
            reject(400, "VALIDATION_FAILED", "Invalid JSON request")
        if request.path.startswith("/api/v1/admin/pricing/policies"):
            check_body(request.body)
    except (InvalidPricingBody, RequestDataTooBig, ValueError):
        reject(422, "VALIDATION_FAILED", "Invalid pricing request body")


def _version(request):
    value = request.headers.get("If-Match", "")
    if not value:
        reject(428, "PRECONDITION_REQUIRED", "A current If-Match ETag is required")
    if not re.fullmatch(r'"[1-9][0-9]*"', value.strip()):
        reject(400, "VALIDATION_FAILED", "Invalid If-Match")
    return int(value.strip()[1:-1])


def _key(request):
    key = request.headers.get("Idempotency-Key", "")
    if not key:
        reject(428, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required")
    if not 16 <= len(key) <= 128 or any(ord(x) < 33 or ord(x) > 126 for x in key) or "," in key:
        reject(422, "VALIDATION_FAILED", "Invalid idempotency key")
    return key


def _policy_response(policy, status=200):
    response = JsonResponse({"data": pricing_admin.encode(policy)}, status=status)
    response["ETag"] = f'"{policy.state_version}"'
    return response


def _permission_snapshot(account_id):
    target = Principal.objects.filter(account_id=account_id, plane="admin").first()
    if not target:
        reject(404, "NOT_FOUND", "Admin account not found")
    return {
        "account_id": str(target.account_id),
        **rbac.snapshot(target),
    }


def register(router):
    @router.get("/rbac/me", operation_id="admin_rbac_me")
    def my_permissions(request):
        return {"data": {"account_id": str(request.auth.account_id), **rbac.snapshot(request.auth)}}

    @router.get("/rbac/catalog", operation_id="admin_rbac_catalog")
    def catalog(request):
        rbac.require(request.auth, "system.settings.read")
        return {
            "data": {
                "capabilities": list(rbac.CAPABILITIES),
                "roles": {k: sorted(v) for k, v in rbac.ROLE_DEFAULTS.items()},
            }
        }

    @router.get("/rbac/accounts/{account_id}", operation_id="admin_rbac_account")
    def account_permissions(request, account_id: UUID):
        rbac.require(request.auth, "system.settings.read")
        return {"data": _permission_snapshot(account_id)}

    @router.post(
        "/rbac/accounts/{account_id}/roles",
        response=AckEnvelope,
        operation_id="admin_rbac_role_add",
    )
    def assign_role(request, account_id: UUID, payload: RoleChange):
        rbac.require(request.auth, "system.settings.manage")
        _json(request)
        return {
            "data": admin_rbac.grant_role(request.auth, account_id, payload.role, payload.reason)
        }

    @router.delete(
        "/rbac/accounts/{account_id}/roles/{role}",
        response=AckEnvelope,
        operation_id="admin_rbac_role_remove",
    )
    def remove_role(request, account_id: UUID, role: str, payload: Reason):
        rbac.require(request.auth, "system.settings.manage")
        _json(request)
        return {"data": admin_rbac.revoke_role(request.auth, account_id, role, payload.reason)}

    @router.put(
        "/rbac/accounts/{account_id}/overrides/{capability}",
        response=AckEnvelope,
        operation_id="admin_rbac_override_set",
    )
    def set_override(request, account_id: UUID, capability: str, payload: OverrideChange):
        rbac.require(request.auth, "system.settings.manage")
        _json(request)
        return {
            "data": admin_rbac.put_override(
                request.auth, account_id, capability, payload.decision, payload.reason
            )
        }

    @router.delete(
        "/rbac/accounts/{account_id}/overrides/{capability}",
        response=AckEnvelope,
        operation_id="admin_rbac_override_remove",
    )
    def remove_override(request, account_id: UUID, capability: str, payload: Reason):
        rbac.require(request.auth, "system.settings.manage")
        _json(request)
        return {
            "data": admin_rbac.clear_override(request.auth, account_id, capability, payload.reason)
        }

    @router.get("/pricing/policies", include_in_schema=False, operation_id="adminPricingList", response={200: PricingListEnvelope})
    def list_pricing(request, status: str = "", limit: int = Query(50, ge=1, le=100)):
        reject(410, "GENERIC_PRICING_RETIRED", "Only Zone composite tariff APIs are supported; use /api/v1/admin/zones/tariffs")
        rbac.require(request.auth, "pricing.read")
        if (
            status not in {"", "DRAFT", "PUBLISHED", "WITHDRAWN"}
            or not 1 <= limit <= 100
            or any(k not in {"status", "limit"} or len(v) != 1 for k, v in request.GET.lists())
        ):
            reject(422, "VALIDATION_FAILED", "Invalid tariff listing filter")
        return {"data": pricing_admin.list_policies(status, limit)}

    @router.post("/pricing/policies", include_in_schema=False, response={201: PricingEnvelope}, operation_id="adminPricingCreateDraft")
    def create_pricing(request, payload: Draft):
        reject(410, "GENERIC_PRICING_RETIRED", "Only Zone composite tariff APIs are supported; use /api/v1/admin/zones/tariffs")
        rbac.require(request.auth, "pricing.manage")
        _json(request)
        key = _key(request)

        def create():
            policy = pricing_admin.create(request.auth, payload)
            return 201, {"data": pricing_admin.encode(policy)}

        status, body = idempotency.execute(
            request.auth,
            "POST /api/v1/admin/pricing/policies",
            key,
            payload.model_dump(mode="json"),
            create,
        )
        response = JsonResponse(body, status=status)
        response["ETag"] = f'"{body["data"]["state_version"]}"'
        return response

    @router.get("/pricing/policies/{policy_id}", include_in_schema=False, operation_id="adminPricingGet", response={200: PricingEnvelope})
    def get_pricing(request, policy_id: UUID):
        reject(410, "GENERIC_PRICING_RETIRED", "Only Zone composite tariff APIs are supported; use /api/v1/admin/zones/tariffs")
        rbac.require(request.auth, "pricing.read")
        return _policy_response(pricing_admin.get(policy_id))

    @router.get("/pricing/policies/{policy_id}/history", include_in_schema=False, operation_id="adminPricingHistory", response={200: PricingHistoryEnvelope})
    def pricing_history(request, policy_id: UUID):
        reject(410, "GENERIC_PRICING_RETIRED", "Only Zone composite tariff APIs are supported; use /api/v1/admin/zones/tariffs")
        rbac.require(request.auth, "pricing.read")
        rbac.require(request.auth, "audit.read")
        return {"data": pricing_admin.history(policy_id)}

    def transition(request, policy_id, payload, operation):
        rbac.require(request.auth, "pricing.manage")
        _json(request)
        version = _version(request)
        key = _key(request)

        def modify():
            func = pricing_admin.publish if operation == "publish" else pricing_admin.withdraw
            obj = func(request.auth, policy_id, version, payload.reason)
            return 200, {"data": pricing_admin.encode(obj)}

        try:
            status, body = idempotency.execute(
                request.auth,
                f"POST /api/v1/admin/pricing/policies/{policy_id}/{operation} {version}",
                key,
                payload.model_dump(mode="json"),
                modify,
            )
        except IntegrityError:
            reject(
                409, "PRICING_SCOPE_CONFLICT", "Tariff mutation conflicts with durable constraints"
            )
        response = JsonResponse(body, status=status)
        response["ETag"] = f'"{body["data"]["state_version"]}"'
        return response

    @router.post("/pricing/policies/{policy_id}/publish", include_in_schema=False, operation_id="adminPricingPublish", response={200: PricingEnvelope})
    def publish_pricing(request, policy_id: UUID, payload: Reason):
        reject(410, "GENERIC_PRICING_RETIRED", "Only Zone composite tariff APIs are supported; use /api/v1/admin/zones/tariffs")
        return transition(request, policy_id, payload, "publish")

    @router.post("/pricing/policies/{policy_id}/withdraw", include_in_schema=False, operation_id="adminPricingWithdraw", response={200: PricingEnvelope})
    def withdraw_pricing(request, policy_id: UUID, payload: Reason):
        reject(410, "GENERIC_PRICING_RETIRED", "Only Zone composite tariff APIs are supported; use /api/v1/admin/zones/tariffs")
        return transition(request, policy_id, payload, "withdraw")
