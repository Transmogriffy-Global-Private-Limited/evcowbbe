"""Customer / User service layer.

Orchestrates business logic, idempotency, version/ETag verification, and transaction
boundaries for all Customer-facing operations.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable
from uuid import UUID, uuid4

from django.http import JsonResponse
from pydantic import ValidationError as PydanticValidationError

from cow.common.errors import reject
from cow.models import Principal
from cow.services import idempotency
from cow.users import repository as repo
from cow.users.schemas import (
    AddressCreate,
    AddressPatch,
    JobCancelPayload,
    NotificationPreferencesPayload,
    ProfilePatch,
    PromoteAddressPayload,
    PromoteVehiclePayload,
    RatingCreatePayload,
    RealtimeTicketPayload,
    ServiceRequestPayload,
    SupportReplyPayload,
    SupportTicketCreatePayload,
    VehicleCreate,
    VehiclePatch,
    WalletRechargeOrderPayload,
    WalletRechargeVerifyPayload,
)


# ============================================================================
# Request & Precondition Helpers
# ============================================================================

def parse_payload(model: Any, payload: Any) -> Any:
    """Validate payload against a Pydantic/Ninja schema or reject with 422."""
    try:
        return model.model_validate(payload)
    except PydanticValidationError:
        reject(422, "VALIDATION_FAILED", "Payload fields are invalid")


def ensure_json(request: Any) -> None:
    """Ensure Content-Type is application/json or reject with 415."""
    ct = request.content_type.split(";", 1)[0].lower() if hasattr(request, "content_type") else ""
    if ct != "application/json":
        reject(415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")


def get_idempotency_key(request: Any, *, required: bool = False) -> str:
    """Extract and validate the Idempotency-Key header."""
    key = request.headers.get("Idempotency-Key", "") if hasattr(request, "headers") else ""
    if not key and required:
        reject(428, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required")
    if key and (not 16 <= len(key) <= 128 or not re.fullmatch(r"[!-~]+", key) or "," in key):
        reject(422, "VALIDATION_FAILED", "Invalid Idempotency-Key")
    return key


def get_version_etag(request: Any) -> int:
    """Extract and parse the required quoted integer If-Match ETag."""
    etag = request.headers.get("If-Match", "") if hasattr(request, "headers") else ""
    if not etag:
        reject(428, "PRECONDITION_REQUIRED", "Current If-Match ETag required")
    match = re.fullmatch(r'"(0|[1-9][0-9]*)"', etag.strip())
    if not match:
        reject(400, "VALIDATION_FAILED", "Invalid If-Match format; expected quoted integer")
    return int(match.group(1))


def as_response(status: int, data: Any, *, etag: int | None = None) -> JsonResponse:
    """Build a standard JSON response with optional quoted ETag."""
    response = JsonResponse({"data": data}, status=status)
    if etag is not None:
        response["ETag"] = f'"{etag}"'
    return response


def execute_idempotent(
    actor: Principal,
    scope: str,
    key: str,
    payload: dict[str, Any],
    op: Callable[[], tuple[int, dict[str, Any]]],
) -> JsonResponse:
    """Execute operation with optional idempotency receipt and response formatting."""
    if not key:
        status, data = op()
    else:
        status, data = idempotency.execute(actor, scope, key, payload, op)
    result = data["data"]
    etag = result.get("version") if isinstance(result, dict) else None
    return as_response(status, result, etag=etag)


# ============================================================================
# Profile Services
# ============================================================================

def get_profile(actor: Principal) -> dict[str, Any]:
    return repo.get_profile(actor)


def edit_profile(actor: Principal, raw_payload: dict[str, Any], version: int, key: str) -> JsonResponse:
    patch = parse_payload(ProfilePatch, raw_payload)
    patch_dump = patch.model_dump(exclude_unset=True)

    def op():
        res = repo.update_profile(actor, patch_dump, version)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        "PATCH /app/profile",
        key,
        {"etag": version, **patch.model_dump(mode="json", exclude_unset=True)},
        op,
    )


# ============================================================================
# Vehicle Services
# ============================================================================

def list_vehicles(actor: Principal) -> list[dict[str, Any]]:
    return repo.list_vehicles(actor)


def get_vehicle(actor: Principal, vehicle_id: UUID) -> tuple[dict[str, Any], int]:
    data = repo.get_vehicle(actor, vehicle_id)
    return data, data["version"]


def create_vehicle(actor: Principal, raw_payload: dict[str, Any], key: str) -> JsonResponse:
    data = parse_payload(VehicleCreate, raw_payload)

    def op():
        res = repo.create_vehicle(actor, data.model_dump())
        return 201, {"data": res}

    return execute_idempotent(
        actor,
        "POST /app/vehicles",
        key,
        data.model_dump(mode="json"),
        op,
    )


def edit_vehicle(actor: Principal, vehicle_id: UUID, raw_payload: dict[str, Any], version: int, key: str) -> JsonResponse:
    patch = parse_payload(VehiclePatch, raw_payload)

    def op():
        res = repo.update_vehicle(actor, vehicle_id, patch.model_dump(exclude_unset=True), version)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        f"PATCH /app/vehicles/{vehicle_id}",
        key,
        {"etag": version, **patch.model_dump(mode="json", exclude_unset=True)},
        op,
    )


def retire_vehicle(actor: Principal, vehicle_id: UUID, version: int, key: str) -> JsonResponse:
    def op():
        res = repo.retire_vehicle(actor, vehicle_id, version)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        f"DELETE /app/vehicles/{vehicle_id}",
        key,
        {"etag": version, "vehicle_id": str(vehicle_id)},
        op,
    )


# ============================================================================
# Address Services
# ============================================================================

def list_addresses(actor: Principal) -> list[dict[str, Any]]:
    return repo.list_addresses(actor)


def get_address(actor: Principal, address_id: UUID) -> tuple[dict[str, Any], int]:
    data = repo.get_address(actor, address_id)
    return data, data["version"]


def create_address(actor: Principal, raw_payload: dict[str, Any], key: str) -> JsonResponse:
    data = parse_payload(AddressCreate, raw_payload)

    def op():
        res = repo.create_address(actor, data.model_dump())
        return 201, {"data": res}

    return execute_idempotent(
        actor,
        "POST /app/addresses",
        key,
        data.model_dump(mode="json"),
        op,
    )


def edit_address(actor: Principal, address_id: UUID, raw_payload: dict[str, Any], version: int, key: str) -> JsonResponse:
    patch = parse_payload(AddressPatch, raw_payload)

    def op():
        res = repo.update_address(actor, address_id, patch.model_dump(exclude_unset=True), version)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        f"PATCH /app/addresses/{address_id}",
        key,
        {"etag": version, **patch.model_dump(mode="json", exclude_unset=True)},
        op,
    )


def retire_address(actor: Principal, address_id: UUID, version: int, key: str) -> JsonResponse:
    def op():
        res = repo.retire_address(actor, address_id, version)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        f"DELETE /app/addresses/{address_id}",
        key,
        {"etag": version, "address_id": str(address_id)},
        op,
    )


def make_default_address(actor: Principal, address_id: UUID, version: int, key: str) -> JsonResponse:
    def op():
        res = repo.make_default_address(actor, address_id, version)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        f"POST /app/addresses/{address_id}/make-default",
        key,
        {"etag": version, "address_id": str(address_id)},
        op,
    )


# ============================================================================
# Service Request Services
# ============================================================================

def create_service_request(actor: Principal, raw_payload: dict[str, Any], key: str) -> JsonResponse:
    payload = parse_payload(ServiceRequestPayload, raw_payload)

    def op():
        res = repo.create_service_request(actor, payload.vehicle_id, payload.address_id, payload.requested_energy_wh)
        return 201, {"data": res}

    return execute_idempotent(
        actor,
        "service-request",
        key,
        payload.model_dump(mode="json"),
        op,
    )


def get_service_request(actor: Principal, request_id: UUID) -> dict[str, Any]:
    return repo.get_service_request(actor, request_id)


def list_service_requests(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    return repo.list_service_requests(actor, limit)


# ============================================================================
# Service Job Services
# ============================================================================

def get_job(actor: Principal, job_id: UUID) -> tuple[dict[str, Any], int]:
    job_data = repo.get_job(actor, job_id)
    return job_data, job_data["version"]


def list_jobs(actor: Principal, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
    return repo.list_jobs(actor, limit, state)


def cancel_job(actor: Principal, job_id: UUID, raw_payload: dict[str, Any], version: int, key: str) -> JsonResponse:
    payload = parse_payload(JobCancelPayload, raw_payload)

    def op():
        res = repo.cancel_job(actor, job_id, version, payload.reason)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        f"POST /app/jobs/{job_id}/cancel",
        key,
        {"etag": version, **payload.model_dump(mode="json", exclude_unset=True)},
        op,
    )


def get_job_live(actor: Principal, job_id: UUID) -> dict[str, Any]:
    return repo.get_job_live(actor, job_id)


# ============================================================================
# Snapshot Promotion Services
# ============================================================================

def promote_vehicle(actor: Principal, job_id: UUID, raw_payload: dict[str, Any], key: str) -> JsonResponse:
    payload = parse_payload(PromoteVehiclePayload, raw_payload)

    def op():
        res = repo.promote_vehicle_from_job(actor, job_id, payload.label)
        return 201, {"data": res}

    return execute_idempotent(
        actor,
        f"POST /app/jobs/{job_id}/vehicle/promote",
        key,
        payload.model_dump(mode="json"),
        op,
    )


def promote_address(actor: Principal, job_id: UUID, raw_payload: dict[str, Any], key: str) -> JsonResponse:
    payload = parse_payload(PromoteAddressPayload, raw_payload)

    def op():
        res = repo.promote_address_from_job(actor, job_id, payload.label, payload.make_default)
        return 201, {"data": res}

    return execute_idempotent(
        actor,
        f"POST /app/jobs/{job_id}/address/promote",
        key,
        payload.model_dump(mode="json"),
        op,
    )


# ============================================================================
# Rating Services
# ============================================================================

def get_job_rating(actor: Principal, job_id: UUID) -> dict[str, Any]:
    return repo.get_rating_for_job(actor, job_id)


def upsert_job_rating(
    actor: Principal, job_id: UUID, raw_payload: dict[str, Any], version: int | None, key: str
) -> JsonResponse:
    payload = parse_payload(RatingCreatePayload, raw_payload)
    idempotency_payload = {**payload.model_dump(mode="json"), "if_match_version": version}

    def op():
        res = repo.upsert_rating_for_job(actor, job_id, payload.rating, payload.comment, version=version)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        f"PUT /app/jobs/{job_id}/rating",
        key,
        idempotency_payload,
        op,
    )


def list_ratings(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    return repo.list_ratings(actor, limit)


# ============================================================================
# Support Ticket Services
# ============================================================================

def create_support_ticket(actor: Principal, raw_payload: dict[str, Any], key: str) -> JsonResponse:
    payload = parse_payload(SupportTicketCreatePayload, raw_payload)

    def op():
        res = repo.create_support_ticket(
            actor, payload.category, payload.subject, payload.message, payload.job_id
        )
        return 201, {"data": res}

    return execute_idempotent(
        actor,
        "POST /app/support/tickets",
        key,
        payload.model_dump(mode="json"),
        op,
    )


def get_support_ticket(actor: Principal, ticket_id: UUID) -> dict[str, Any]:
    return repo.get_support_ticket(actor, ticket_id)


def list_support_tickets(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    return repo.list_support_tickets(actor, limit)


def reply_support_ticket(
    actor: Principal, ticket_id: UUID, raw_payload: dict[str, Any], version: int | None, key: str
) -> JsonResponse:
    payload = parse_payload(SupportReplyPayload, raw_payload)
    idempotency_payload = {**payload.model_dump(mode="json"), "if_match_version": version}

    def op():
        res = repo.reply_support_ticket(actor, ticket_id, payload.message, version=version)
        return 200, {"data": res}

    return execute_idempotent(
        actor,
        f"POST /app/support/tickets/{ticket_id}/replies",
        key,
        idempotency_payload,
        op,
    )


# ============================================================================
# Notification & Preference Services
# ============================================================================

def list_notifications(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    return repo.list_notifications(actor, limit)


def get_unread_notification_count(actor: Principal) -> dict[str, int]:
    count = repo.get_unread_notification_count(actor)
    return {"unread_count": count}


def mark_notification_read(actor: Principal, notification_id: UUID) -> dict[str, Any]:
    return repo.mark_notification_read(actor, notification_id)


def mark_all_notifications_read(actor: Principal) -> list[dict[str, Any]]:
    return repo.mark_all_notifications_read(actor)


def get_notification_preferences(actor: Principal) -> dict[str, Any]:
    return repo.get_notification_preferences(actor)


def update_notification_preferences(actor: Principal, raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = parse_payload(NotificationPreferencesPayload, raw_payload)
    return repo.update_notification_preferences(actor, payload.model_dump(exclude_unset=True))


# ============================================================================
# Realtime SSE Ticket Services
# ============================================================================

def create_realtime_ticket(actor: Principal, raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = parse_payload(RealtimeTicketPayload, raw_payload)
    return repo.create_realtime_ticket(actor, payload.subscriptions, payload.resume_after)


# ============================================================================
# Wallet & Payment Recharge Services
# ============================================================================

def get_razorpay_credentials() -> tuple[str, str]:
    """Retrieve configured Razorpay Key ID and Key Secret."""
    key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not secret:
        try:
            from scripts.dev import env_file
            env_file()
            key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
            secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
        except Exception:
            pass
    return key_id, secret


def call_razorpay_create_order(key_id: str, secret: str, amount_paise: int, currency: str, receipt: str) -> str:
    """Create Razorpay payment order via official Orders API."""
    if not key_id or not secret:
        reject(500, "CONFIGURATION_ERROR", "Payment gateway credentials are not configured")

    import base64
    import json
    import urllib.error
    import urllib.request

    url = "https://api.razorpay.com/v1/orders"
    payload = json.dumps({
        "amount": amount_paise,
        "currency": currency,
        "receipt": receipt,
        "payment_capture": 1,
    }).encode("utf-8")
    auth_str = f"{key_id}:{secret}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {b64_auth}",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            order_id = res_data.get("id")
            if not order_id:
                reject(502, "PAYMENT_GATEWAY_ERROR", "Razorpay did not return an order ID")
            return order_id
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        reject(502, "PAYMENT_GATEWAY_ERROR", f"Payment provider rejected order: {err_msg}")
    except (urllib.error.URLError, OSError) as e:
        reject(504, "GATEWAY_TIMEOUT", f"Payment gateway connection timed out or unreachable: {e}")


def fetch_razorpay_payment(key_id: str, secret: str, payment_id: str) -> dict[str, Any]:
    """Fetch authoritative payment status from Razorpay."""
    if not key_id or not secret:
        reject(500, "CONFIGURATION_ERROR", "Payment gateway credentials are not configured")

    import base64
    import json
    import urllib.error
    import urllib.request

    url = f"https://api.razorpay.com/v1/payments/{payment_id}"
    auth_str = f"{key_id}:{secret}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {b64_auth}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        reject(502, "PAYMENT_GATEWAY_ERROR", f"Unable to fetch payment details from provider: {err_msg}")
    except (urllib.error.URLError, OSError) as e:
        reject(504, "GATEWAY_TIMEOUT", f"Payment gateway connection timed out: {e}")


def get_wallet(actor: Principal) -> tuple[dict[str, Any], int]:
    """Retrieve customer's wallet balance and version."""
    return repo.get_or_create_wallet(actor)


def list_wallet_transactions(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve customer's wallet transaction ledger."""
    return repo.list_wallet_transactions(actor, limit=limit)


def create_wallet_recharge_order(actor: Principal, raw_payload: dict[str, Any], key: str) -> JsonResponse:
    """Create a new Razorpay payment order for wallet recharge."""
    payload = parse_payload(WalletRechargeOrderPayload, raw_payload)
    p = payload.model_dump(mode="json")
    key_id, secret = get_razorpay_credentials()

    def op():
        # Stable deterministic receipt per (actor, idempotency key), bounded to 40 chars for Razorpay API
        actor_part = actor.id.hex[:8] if hasattr(actor, "id") and hasattr(actor.id, "hex") else str(actor.id)[:8]
        clean_key = re.sub(r"[^a-zA-Z0-9_-]", "", key)[:16]
        receipt = f"rc_{actor_part}_{clean_key}"[:40]

        # Order recovery: check if order was already recorded locally for this receipt
        existing = repo.get_payment_order_by_receipt(actor, receipt, key_id=key_id)
        if existing:
            return 201, {"data": existing}

        provider_order_id = call_razorpay_create_order(
            key_id=key_id,
            secret=secret,
            amount_paise=payload.amount_paise,
            currency=payload.currency,
            receipt=receipt,
        )
        data = repo.create_payment_order(
            actor=actor,
            amount_paise=payload.amount_paise,
            currency=payload.currency,
            provider_order_id=provider_order_id,
            key_id=key_id,
            receipt=receipt,
        )
        return 201, {"data": data}

    return execute_idempotent(actor, "wallet-recharge-order", key, p, op)


def verify_wallet_recharge(actor: Principal, raw_payload: dict[str, Any]) -> dict[str, Any]:
    """Verify payment signature and authoritative captured status from Razorpay before crediting."""
    payload = parse_payload(WalletRechargeVerifyPayload, raw_payload)
    key_id, secret = get_razorpay_credentials()
    if not secret:
        reject(500, "CONFIGURATION_ERROR", "Razorpay key secret is not configured")

    # Authoritative payment evidence from Razorpay
    payment_evidence = fetch_razorpay_payment(key_id, secret, payload.razorpay_payment_id)

    return repo.verify_and_credit_recharge(
        actor=actor,
        order_id=payload.razorpay_order_id,
        payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
        secret=secret,
        payment_evidence=payment_evidence,
    )

