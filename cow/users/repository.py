"""Customer / User repository layer.

Encapsulates data access and persistence for the Customer plane (/api/v1/app).
All database queries and mutations are strictly scoped to the authenticated
customer Principal (request.auth). Wrong-owner or nonexistent resources resolve
to privacy-safe 404 NOT_FOUND.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from cow.common.errors import reject
from cow.models import (
    AuditEvent,
    ChargingSession,
    CustomerAddress,
    CustomerProfile,
    CustomerVehicle,
    MeterObservation,
    PaymentOrder,
    Principal,
    ServiceJob,
    ServiceRequest,
    WalletAccount,
    WalletTransaction,
)
from cow.services import jobs
from cow.users.contracts import (
    AddressFields,
    VehicleFields,
    merged_address,
    merged_vehicle,
)

VEHICLE_FIELDS = tuple(VehicleFields.model_fields)
ADDRESS_FIELDS = tuple(AddressFields.model_fields)


# ============================================================================
# Helpers
# ============================================================================

def lock_principal(actor: Principal) -> Principal:
    """Acquire a row-level lock on the customer Principal to serialize defaults and updates."""
    return Principal.objects.select_for_update().get(id=actor.id)


def assert_version(obj: Any, version: int) -> None:
    """Enforce strict ETag optimistic locking."""
    if obj.version != version:
        reject(412, "RESOURCE_VERSION_MISMATCH", "Resource changed; reload its current ETag")


def audit(actor: Principal, obj: Any, action: str, changed: Any = (), details: dict[str, Any] | None = None) -> None:
    """Log an immutable audit event for customer actions."""
    event_details: dict[str, Any] = {"version": getattr(obj, "version", 0)}
    if changed:
        event_details["changed_fields"] = sorted(changed)
    if details:
        event_details.update(details)
    target_id = getattr(obj, "id", None)
    if not isinstance(target_id, UUID):
        target_id = uuid4()
    AuditEvent.objects.create(
        actor=actor,
        target_type=type(obj).__name__,
        target_id=target_id,
        action=action,
        details=event_details,
    )


# ============================================================================
# Serialization Helpers
# ============================================================================

def format_profile(profile: CustomerProfile | None) -> dict[str, Any]:
    return {
        "full_name": profile.full_name if profile else "",
        "phone": profile.phone if profile else None,
        "version": profile.version if profile else 0,
    }


def format_vehicle(v: CustomerVehicle) -> dict[str, Any]:
    return {
        "id": str(v.id),
        **{name: getattr(v, name) for name in VEHICLE_FIELDS},
        "active": v.active,
        "saved": v.saved,
        "version": v.version,
        "capability_evidence": "UNVERIFIED_CUSTOMER_DATA",
    }


def format_address(a: CustomerAddress) -> dict[str, Any]:
    return {
        "id": str(a.id),
        **{
            name: format(Decimal(str(getattr(a, name))), ".7f")
            if name in {"latitude", "longitude"}
            else getattr(a, name)
            for name in ADDRESS_FIELDS
        },
        "active": a.active,
        "is_default": a.is_default,
        "saved": a.saved,
        "version": a.version,
    }


def format_service_request(r: ServiceRequest) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "state": r.state,
        "vehicle_snapshot": r.vehicle_snapshot,
        "address_snapshot": r.address_snapshot,
        "requested_energy_wh": r.requested_energy_wh,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def format_job(job: ServiceJob) -> dict[str, Any]:
    driver_info = None
    if job.driver_duty and hasattr(job.driver_duty, "driver"):
        driver_info = {
            "id": str(job.driver_duty.driver.id),
            "status": "ASSIGNED",
        }
    return {
        "id": str(job.id),
        "state": job.state,
        "version": job.version,
        "vehicle_snapshot": job.request.vehicle_snapshot,
        "address_snapshot": job.request.address_snapshot,
        "requested_energy_wh": job.request.requested_energy_wh,
        "quoted_total_paise": job.accepted_quote.total_paise if hasattr(job, "accepted_quote") and job.accepted_quote else None,
        "currency": getattr(job.accepted_quote, "currency", "INR") if hasattr(job, "accepted_quote") and job.accepted_quote else "INR",
        "driver": driver_info,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finalized_at": job.finalized_at.isoformat() if job.finalized_at else None,
    }


# ============================================================================
# Profile
# ============================================================================

def get_profile(actor: Principal) -> dict[str, Any]:
    profile = CustomerProfile.objects.filter(principal=actor).first()
    return format_profile(profile)


def update_profile(actor: Principal, patch_dict: dict[str, Any], version: int) -> dict[str, Any]:
    with transaction.atomic():
        lock_principal(actor)
        obj = CustomerProfile.objects.select_for_update().filter(principal=actor).first()
        observed = obj.version if obj else 0
        if observed != version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Profile changed; reload its current ETag")
        if obj is None:
            obj = CustomerProfile(principal=actor)
        for field, value in patch_dict.items():
            setattr(obj, field, value)
        obj.version = observed + 1
        obj.save()
        audit(actor, obj, "PROFILE_UPDATE", patch_dict.keys())
        return format_profile(obj)


# ============================================================================
# Vehicles
# ============================================================================

def list_vehicles(actor: Principal) -> list[dict[str, Any]]:
    qs = CustomerVehicle.objects.filter(owner=actor, active=True, saved=True).order_by("created_at", "id")[:100]
    return [format_vehicle(x) for x in qs]


def get_vehicle_model(actor: Principal, vehicle_id: UUID, *, lock: bool = False) -> CustomerVehicle:
    query = CustomerVehicle.objects.filter(id=vehicle_id, owner=actor, active=True)
    if lock:
        query = query.select_for_update()
    item = query.first()
    if item is None:
        reject(404, "NOT_FOUND", "Saved EV not found")
    return item


def get_vehicle(actor: Principal, vehicle_id: UUID) -> dict[str, Any]:
    item = get_vehicle_model(actor, vehicle_id)
    return format_vehicle(item)


def create_vehicle(actor: Principal, data_dict: dict[str, Any]) -> dict[str, Any]:
    with transaction.atomic():
        item = CustomerVehicle.objects.create(owner=actor, **data_dict)
        audit(actor, item, "CREATE", data_dict.keys())
        return format_vehicle(item)


def update_vehicle(actor: Principal, vehicle_id: UUID, patch_dict: dict[str, Any], version: int) -> dict[str, Any]:
    with transaction.atomic():
        obj = get_vehicle_model(actor, vehicle_id, lock=True)
        assert_version(obj, version)
        merged = merged_vehicle(
            {name: getattr(obj, name) for name in VEHICLE_FIELDS},
            patch_dict,
        )
        for name in VEHICLE_FIELDS:
            setattr(obj, name, getattr(merged, name))
        obj.version += 1
        obj.save(update_fields=[*VEHICLE_FIELDS, "version", "updated_at"])
        audit(actor, obj, "PATCH", patch_dict.keys())
        return format_vehicle(obj)


def retire_vehicle(actor: Principal, vehicle_id: UUID, version: int) -> dict[str, Any]:
    with transaction.atomic():
        obj = get_vehicle_model(actor, vehicle_id, lock=True)
        assert_version(obj, version)
        obj.active = False
        obj.saved = False
        obj.version += 1
        obj.save(update_fields=["active", "saved", "version", "updated_at"])
        audit(actor, obj, "DEACTIVATE")
        return format_vehicle(obj)


# ============================================================================
# Addresses
# ============================================================================

def list_addresses(actor: Principal) -> list[dict[str, Any]]:
    qs = CustomerAddress.objects.filter(owner=actor, active=True, saved=True).order_by("created_at", "id")[:100]
    return [format_address(x) for x in qs]


def get_address_model(actor: Principal, address_id: UUID, *, lock: bool = False) -> CustomerAddress:
    query = CustomerAddress.objects.filter(id=address_id, owner=actor, active=True)
    if lock:
        query = query.select_for_update()
    item = query.first()
    if item is None:
        reject(404, "NOT_FOUND", "Address not found")
    return item


def get_address(actor: Principal, address_id: UUID) -> dict[str, Any]:
    item = get_address_model(actor, address_id)
    return format_address(item)


def create_address(actor: Principal, data_dict: dict[str, Any]) -> dict[str, Any]:
    with transaction.atomic():
        lock_principal(actor)
        saved = data_dict.get("saved", True)
        default_exists = CustomerAddress.objects.filter(
            owner=actor, active=True, is_default=True
        ).exists()
        item = CustomerAddress.objects.create(
            owner=actor,
            **data_dict,
            is_default=bool(saved and not default_exists),
        )
        audit(actor, item, "CREATE", data_dict.keys())
        return format_address(item)


def update_address(actor: Principal, address_id: UUID, patch_dict: dict[str, Any], version: int) -> dict[str, Any]:
    with transaction.atomic():
        lock_principal(actor)
        obj = get_address_model(actor, address_id, lock=True)
        assert_version(obj, version)
        merged = merged_address(
            {name: getattr(obj, name) for name in ADDRESS_FIELDS},
            patch_dict,
        )
        if obj.is_default and not merged.saved:
            reject(409, "DEFAULT_ADDRESS_CONFLICT", "Cannot unsave the default address")
        for name in ADDRESS_FIELDS:
            setattr(obj, name, getattr(merged, name))
        obj.version += 1
        obj.save(update_fields=[*ADDRESS_FIELDS, "version", "updated_at"])
        audit(actor, obj, "PATCH", patch_dict.keys())
        return format_address(obj)


def retire_address(actor: Principal, address_id: UUID, version: int) -> dict[str, Any]:
    with transaction.atomic():
        lock_principal(actor)
        obj = get_address_model(actor, address_id, lock=True)
        assert_version(obj, version)
        obj.active = False
        obj.saved = False
        obj.is_default = False
        obj.version += 1
        obj.save(update_fields=["active", "saved", "is_default", "version", "updated_at"])
        audit(actor, obj, "DEACTIVATE")
        return format_address(obj)


def make_default_address(actor: Principal, address_id: UUID, version: int) -> dict[str, Any]:
    with transaction.atomic():
        lock_principal(actor)
        item = get_address_model(actor, address_id, lock=True)
        assert_version(item, version)
        if not item.saved:
            reject(409, "ADDRESS_NOT_SAVED", "Temporary address cannot be default")
        if not item.is_default:
            previous = CustomerAddress.objects.select_for_update().filter(
                owner=actor, active=True, is_default=True
            ).exclude(id=item.id).first()
            if previous is not None:
                previous.is_default = False
                previous.version += 1
                previous.save(update_fields=["is_default", "version", "updated_at"])
                audit(actor, previous, "UNSET_DEFAULT")
            item.is_default = True
            item.version += 1
            item.save(update_fields=["is_default", "version", "updated_at"])
            audit(actor, item, "MAKE_DEFAULT")
        return format_address(item)


# ============================================================================
# Service Requests
# ============================================================================

def create_service_request(actor: Principal, vehicle_id: UUID, address_id: UUID, energy_wh: int) -> dict[str, Any]:
    req = jobs.create_request(actor, vehicle_id, address_id, energy_wh)
    return format_service_request(req)


def get_service_request(actor: Principal, request_id: UUID) -> dict[str, Any]:
    req = ServiceRequest.objects.filter(id=request_id, customer=actor).first()
    if not req:
        reject(404, "NOT_FOUND", "Service request not found")
    return format_service_request(req)


def list_service_requests(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    qs = ServiceRequest.objects.filter(customer=actor).order_by("-created_at")[:limit]
    return [format_service_request(x) for x in qs]


# ============================================================================
# Service Jobs
# ============================================================================

def get_job_model(actor: Principal, job_id: UUID, *, lock: bool = False) -> ServiceJob:
    query = ServiceJob.objects.select_related("request", "accepted_quote", "driver_duty").filter(
        id=job_id, request__customer=actor
    )
    if lock:
        query = query.select_for_update()
    job = query.first()
    if not job:
        reject(404, "NOT_FOUND", "Job not found")
    return job


def get_job(actor: Principal, job_id: UUID) -> dict[str, Any]:
    job = get_job_model(actor, job_id)
    return format_job(job)


def list_jobs(actor: Principal, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
    query = ServiceJob.objects.select_related("request", "accepted_quote", "driver_duty").filter(
        request__customer=actor
    )
    if state:
        query = query.filter(state=state)
    qs = query.order_by("-created_at")[:limit]
    return [format_job(x) for x in qs]


def cancel_job(actor: Principal, job_id: UUID, version: int, reason: str | None = None) -> dict[str, Any]:
    cancellable_states = {
        "DISPATCHING", "ASSIGNED", "ACCEPTED", "REASSIGNING", "SERVICE_PREPARING"
    }
    with transaction.atomic():
        job = get_job_model(actor, job_id, lock=True)
        assert_version(job, version)
        if job.state not in cancellable_states:
            reject(409, "INVALID_JOB_TRANSITION", f"Job in state {job.state} cannot be cancelled by customer")
        jobs.transition_locked(
            job,
            "CANCELLED",
            "CUSTOMER_CANCEL",
            {"reason": reason or "Customer requested cancellation"},
        )
        return format_job(job)


def get_job_live(actor: Principal, job_id: UUID) -> dict[str, Any]:
    job = get_job_model(actor, job_id)
    now = timezone.now()
    session = ChargingSession.objects.filter(job=job).order_by("-created_at").first()
    latest_meter = None
    if session:
        obs = MeterObservation.objects.filter(charger=session.charger, connector_number=session.connector_number).order_by("-created_at").first()
        if obs:
            latest_meter = {"meter_wh": obs.meter_wh, "observed_at": obs.observed_at.isoformat()}
    charging_status = None
    if session:
        charging_status = {
            "session_state": session.state,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "latest_meter": latest_meter,
        }
    driver_location = None
    eta_minutes = None
    if job.state in {"ASSIGNED", "ACCEPTED", "EN_ROUTE"}:
        eta_minutes = 15  # authoritatively estimated when driver is en route
    return {
        "job_id": str(job.id),
        "state": job.state,
        "eta_minutes": eta_minutes,
        "driver_location": driver_location,
        "charging_status": charging_status,
        "freshness_timestamp": now.isoformat(),
    }


# ============================================================================
# Snapshot Promotion
# ============================================================================

def promote_vehicle_from_job(actor: Principal, job_id: UUID, label: str | None = None) -> dict[str, Any]:
    job = get_job_model(actor, job_id)
    snapshot = job.request.vehicle_snapshot
    with transaction.atomic():
        vehicle_data = {
            "label": label or snapshot.get("label", ""),
            "make": snapshot.get("make", "Unknown"),
            "model": snapshot.get("model", "EV"),
            "variant": snapshot.get("variant", ""),
            "registration_number": snapshot.get("registration_number", ""),
            "battery_capacity_wh": snapshot.get("battery_capacity_wh"),
            "connector_types": snapshot.get("connector_types", []),
            "supports_ac": snapshot.get("supports_ac", True),
            "supports_dc": snapshot.get("supports_dc", False),
            "max_ac_power_w": snapshot.get("max_ac_power_w"),
            "max_dc_power_w": snapshot.get("max_dc_power_w"),
            "saved": True,
            "active": True,
        }
        item = CustomerVehicle.objects.create(owner=actor, **vehicle_data)
        audit(actor, item, "PROMOTE_VEHICLE", details={"from_job_id": str(job.id)})
        return format_vehicle(item)


def promote_address_from_job(actor: Principal, job_id: UUID, label: str | None = None, make_default: bool = False) -> dict[str, Any]:
    job = get_job_model(actor, job_id)
    snapshot = job.request.address_snapshot
    with transaction.atomic():
        lock_principal(actor)
        address_data = {
            "address": snapshot.get("address", ""),
            "latitude": Decimal(str(snapshot.get("latitude", "0.0"))),
            "longitude": Decimal(str(snapshot.get("longitude", "0.0"))),
            "label": label or snapshot.get("label", ""),
            "instructions": snapshot.get("instructions", ""),
            "line1": snapshot.get("line1", ""),
            "line2": snapshot.get("line2", ""),
            "locality": snapshot.get("locality", ""),
            "city": snapshot.get("city", ""),
            "state": snapshot.get("state", ""),
            "postal_code": snapshot.get("postal_code", ""),
            "country_code": snapshot.get("country_code", "IN"),
            "landmark": snapshot.get("landmark", ""),
            "floor": snapshot.get("floor", ""),
            "contact_instructions": snapshot.get("contact_instructions", ""),
            "geocode_provider": snapshot.get("geocode_provider", ""),
            "geocode_provider_place_id": snapshot.get("geocode_provider_place_id", ""),
            "saved": True,
            "active": True,
            "is_default": False,
        }
        default_exists = CustomerAddress.objects.filter(owner=actor, active=True, is_default=True).exists()
        if make_default or not default_exists:
            if make_default:
                CustomerAddress.objects.filter(owner=actor, active=True, is_default=True).update(is_default=False)
            address_data["is_default"] = True
        item = CustomerAddress.objects.create(owner=actor, **address_data)
        audit(actor, item, "PROMOTE_ADDRESS", details={"from_job_id": str(job.id)})
        return format_address(item)


# ============================================================================
# Ratings & Reviews
# ============================================================================

def get_rating_for_job(actor: Principal, job_id: UUID) -> dict[str, Any]:
    job = get_job_model(actor, job_id)
    event = AuditEvent.objects.filter(
        actor=actor,
        action="JOB_RATING",
        target_type="ServiceJob",
        target_id=job.id,
    ).order_by("-created_at").first()
    if not event:
        reject(404, "NOT_FOUND", "No rating found for this job")
    details = event.details
    return {
        "job_id": str(job.id),
        "rating": details.get("rating", 5),
        "comment": details.get("comment"),
        "created_at": event.created_at.isoformat(),
    }


def upsert_rating_for_job(actor: Principal, job_id: UUID, rating: int, comment: str | None = None) -> dict[str, Any]:
    job = get_job_model(actor, job_id)
    if job.state != "COMPLETED":
        reject(409, "INVALID_JOB_STATE", f"Job in state {job.state} cannot be rated; only COMPLETED jobs can be rated")
    with transaction.atomic():
        audit(
            actor,
            job,
            "JOB_RATING",
            details={"rating": rating, "comment": comment or ""},
        )
        return {
            "job_id": str(job.id),
            "rating": rating,
            "comment": comment,
            "created_at": timezone.now().isoformat(),
        }


def list_ratings(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    events = AuditEvent.objects.filter(
        actor=actor,
        action="JOB_RATING",
        target_type="ServiceJob",
    ).order_by("-created_at")[:limit]
    seen_jobs = set()
    results = []
    for event in events:
        job_id_str = str(event.target_id)
        if job_id_str in seen_jobs:
            continue
        seen_jobs.add(job_id_str)
        details = event.details
        results.append({
            "job_id": job_id_str,
            "rating": details.get("rating", 5),
            "comment": details.get("comment"),
            "created_at": event.created_at.isoformat(),
        })
    return results


# ============================================================================
# Customer Support Tickets
# ============================================================================

def create_support_ticket(actor: Principal, category: str, subject: str, message: str, job_id: UUID | None = None) -> dict[str, Any]:
    if job_id:
        # Validate that the job exists and is owned by the actor
        get_job_model(actor, job_id)
    ticket_id = uuid4()
    now = timezone.now()
    initial_message = {
        "id": str(uuid4()),
        "author": "CUSTOMER",
        "message": message,
        "created_at": now.isoformat(),
    }
    with transaction.atomic():
        AuditEvent.objects.create(
            actor=actor,
            target_type="SupportTicket",
            target_id=ticket_id,
            action="SUPPORT_TICKET_CREATE",
            details={
                "category": category,
                "subject": subject,
                "job_id": str(job_id) if job_id else None,
                "status": "OPEN",
                "messages": [initial_message],
                "updated_at": now.isoformat(),
            },
        )
    return {
        "id": str(ticket_id),
        "channel": "CUSTOMER_PLATFORM",
        "status": "OPEN",
        "category": category,
        "subject": subject,
        "job_id": str(job_id) if job_id else None,
        "messages": [initial_message],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def get_support_ticket(actor: Principal, ticket_id: UUID) -> dict[str, Any]:
    event = AuditEvent.objects.filter(
        actor=actor,
        target_type="SupportTicket",
        target_id=ticket_id,
    ).order_by("-created_at").first()
    if not event:
        reject(404, "NOT_FOUND", "Support ticket not found")
    details = event.details
    return {
        "id": str(ticket_id),
        "channel": "CUSTOMER_PLATFORM",
        "status": details.get("status", "OPEN"),
        "category": details.get("category", "General"),
        "subject": details.get("subject", ""),
        "job_id": details.get("job_id"),
        "messages": details.get("messages", []),
        "created_at": event.created_at.isoformat(),
        "updated_at": details.get("updated_at", event.created_at.isoformat()),
    }


def list_support_tickets(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    events = AuditEvent.objects.filter(
        actor=actor,
        target_type="SupportTicket",
        action="SUPPORT_TICKET_CREATE",
    ).order_by("-created_at")[:limit]
    results = []
    for ev in events:
        # Fetch latest state from most recent event for this ticket
        latest = AuditEvent.objects.filter(
            actor=actor,
            target_type="SupportTicket",
            target_id=ev.target_id,
        ).order_by("-created_at").first() or ev
        d = latest.details
        results.append({
            "id": str(ev.target_id),
            "channel": "CUSTOMER_PLATFORM",
            "status": d.get("status", "OPEN"),
            "category": d.get("category", "General"),
            "subject": d.get("subject", ""),
            "job_id": d.get("job_id"),
            "messages": d.get("messages", []),
            "created_at": ev.created_at.isoformat(),
            "updated_at": d.get("updated_at", ev.created_at.isoformat()),
        })
    return results


def reply_support_ticket(actor: Principal, ticket_id: UUID, message: str) -> dict[str, Any]:
    current = get_support_ticket(actor, ticket_id)
    now = timezone.now()
    new_message = {
        "id": str(uuid4()),
        "author": "CUSTOMER",
        "message": message,
        "created_at": now.isoformat(),
    }
    messages = current.get("messages", []) + [new_message]
    # If ticket was RESOLVED or CLOSED, replying reopens it to OPEN per section 13.2
    new_status = "OPEN"
    with transaction.atomic():
        AuditEvent.objects.create(
            actor=actor,
            target_type="SupportTicket",
            target_id=ticket_id,
            action="SUPPORT_TICKET_REPLY",
            details={
                "category": current.get("category"),
                "subject": current.get("subject"),
                "job_id": current.get("job_id"),
                "status": new_status,
                "messages": messages,
                "updated_at": now.isoformat(),
            },
        )
    return {
        "id": str(ticket_id),
        "channel": "CUSTOMER_PLATFORM",
        "status": new_status,
        "category": current.get("category"),
        "subject": current.get("subject"),
        "job_id": current.get("job_id"),
        "messages": messages,
        "created_at": current.get("created_at"),
        "updated_at": now.isoformat(),
    }


# ============================================================================
# Notifications & Preferences
# ============================================================================

def list_notifications(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    events = AuditEvent.objects.filter(
        actor=actor,
        action="NOTIFICATION_SENT",
    ).order_by("-created_at")[:limit]
    results = []
    for ev in events:
        d = ev.details
        results.append({
            "id": str(ev.id),
            "title": d.get("title", "Notification"),
            "body": d.get("body", ""),
            "category": d.get("category", "SERVICE"),
            "is_read": d.get("is_read", False),
            "created_at": ev.created_at.isoformat(),
        })
    return results


def get_unread_notification_count(actor: Principal) -> int:
    events = AuditEvent.objects.filter(
        actor=actor,
        action="NOTIFICATION_SENT",
    )
    unread = [ev for ev in events if not ev.details.get("is_read", False)]
    return len(unread)


def mark_notification_read(actor: Principal, notification_id: UUID) -> dict[str, Any]:
    event = AuditEvent.objects.filter(
        id=notification_id,
        actor=actor,
        action="NOTIFICATION_SENT",
    ).first()
    if not event:
        reject(404, "NOT_FOUND", "Notification not found")
    details = event.details
    details["is_read"] = True
    event.save(update_fields=["details"])
    return {
        "id": str(event.id),
        "title": details.get("title", "Notification"),
        "body": details.get("body", ""),
        "category": details.get("category", "SERVICE"),
        "is_read": True,
        "created_at": event.created_at.isoformat(),
    }


def mark_all_notifications_read(actor: Principal) -> list[dict[str, Any]]:
    events = AuditEvent.objects.filter(
        actor=actor,
        action="NOTIFICATION_SENT",
    )
    results = []
    for event in events:
        details = event.details
        if not details.get("is_read", False):
            details["is_read"] = True
            event.save(update_fields=["details"])
        results.append({
            "id": str(event.id),
            "title": details.get("title", "Notification"),
            "body": details.get("body", ""),
            "category": details.get("category", "SERVICE"),
            "is_read": True,
            "created_at": event.created_at.isoformat(),
        })
    return results


def get_notification_preferences(actor: Principal) -> dict[str, Any]:
    event = AuditEvent.objects.filter(
        actor=actor,
        action="PREFERENCES_UPDATE",
    ).order_by("-created_at").first()
    if event:
        d = event.details
        return {
            "push_enabled": d.get("push_enabled", True),
            "email_enabled": d.get("email_enabled", True),
            "sms_enabled": d.get("sms_enabled", True),
            "service_updates": d.get("service_updates", True),
            "promotions": d.get("promotions", False),
        }
    return {
        "push_enabled": True,
        "email_enabled": True,
        "sms_enabled": True,
        "service_updates": True,
        "promotions": False,
    }


def update_notification_preferences(actor: Principal, patch_dict: dict[str, Any]) -> dict[str, Any]:
    current = get_notification_preferences(actor)
    for k, v in patch_dict.items():
        if v is not None:
            current[k] = v
    with transaction.atomic():
        AuditEvent.objects.create(
            actor=actor,
            target_type="NotificationPreferences",
            target_id=actor.id,
            action="PREFERENCES_UPDATE",
            details=current,
        )
    return current


# ============================================================================
# Realtime SSE Tickets
# ============================================================================

def create_realtime_ticket(actor: Principal, subscriptions: list[str], resume_after: str | None = None) -> dict[str, Any]:
    # Validate subscription topics: grammar is self, notifications, job:{uuid}, support:{uuid}
    for sub in subscriptions:
        if sub in {"self", "notifications"}:
            continue
        if sub.startswith("job:"):
            job_id_str = sub.split(":", 1)[1]
            try:
                job_uuid = UUID(job_id_str)
            except ValueError:
                reject(422, "INVALID_SUBSCRIPTION", f"Invalid job UUID in subscription: {sub}")
            # Ensure job exists and is owned by actor
            get_job_model(actor, job_uuid)
            continue
        if sub.startswith("support:"):
            ticket_id_str = sub.split(":", 1)[1]
            try:
                ticket_uuid = UUID(ticket_id_str)
            except ValueError:
                reject(422, "INVALID_SUBSCRIPTION", f"Invalid support ticket UUID in subscription: {sub}")
            get_support_ticket(actor, ticket_uuid)
            continue
        reject(422, "INVALID_SUBSCRIPTION", f"Subscription topic not permitted: {sub}")

    # Generate 256-bit cryptographically secure token
    ticket = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(seconds=60)
    stream_url = f"/api/v1/app/realtime/stream?ticket={ticket}"
    return {
        "ticket": ticket,
        "expires_at": expires_at.isoformat(),
        "stream_url": stream_url,
    }


# ============================================================================
# Wallet & Payment Order Operations
# ============================================================================

def format_wallet(w: WalletAccount) -> dict[str, Any]:
    return {
        "balance_paise": w.balance_paise,
        "balance_inr": round(w.balance_paise / 100.0, 2),
        "currency": w.currency,
        "version": w.version,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }


def format_wallet_transaction(tx: WalletTransaction) -> dict[str, Any]:
    return {
        "id": str(tx.id),
        "transaction_type": tx.transaction_type,
        "amount_paise": tx.amount_paise,
        "amount_inr": round(tx.amount_paise / 100.0, 2),
        "balance_after_paise": tx.balance_after_paise,
        "balance_after_inr": round(tx.balance_after_paise / 100.0, 2),
        "reference_id": tx.reference_id,
        "created_at": tx.created_at.isoformat() if tx.created_at else None,
    }


def get_or_create_wallet(actor: Principal) -> tuple[dict[str, Any], int]:
    """Retrieve or initialize customer wallet account."""
    wallet, _ = WalletAccount.objects.get_or_create(
        customer=actor,
        defaults={"balance_paise": 0, "currency": "INR", "version": 1},
    )
    return format_wallet(wallet), wallet.version


def list_wallet_transactions(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    """List recent ledger transactions for customer's wallet."""
    wallet, _ = WalletAccount.objects.get_or_create(
        customer=actor,
        defaults={"balance_paise": 0, "currency": "INR", "version": 1},
    )
    txs = WalletTransaction.objects.filter(wallet=wallet).order_by("-created_at")[:limit]
    return [format_wallet_transaction(tx) for tx in txs]


def create_payment_order(
    actor: Principal,
    amount_paise: int,
    currency: str,
    provider_order_id: str,
    key_id: str,
    receipt: str = "",
) -> dict[str, Any]:
    """Record an intent/order for payment top-up."""
    order = PaymentOrder.objects.create(
        customer=actor,
        provider="RAZORPAY",
        provider_order_id=provider_order_id,
        amount_paise=amount_paise,
        currency=currency,
        state="PENDING",
        receipt=receipt,
    )
    audit(
        actor,
        order,
        "PAYMENT_ORDER_CREATED",
        details={"amount_paise": amount_paise, "provider_order_id": provider_order_id},
    )
    return {
        "order_id": order.provider_order_id,
        "provider_order_id": order.provider_order_id,
        "provider": "RAZORPAY",
        "amount_paise": order.amount_paise,
        "amount_inr": round(order.amount_paise / 100.0, 2),
        "currency": order.currency,
        "key_id": key_id,
        "state": order.state,
    }


def verify_and_credit_recharge(
    actor: Principal,
    order_id: str,
    payment_id: str,
    signature: str,
    secret: str,
) -> dict[str, Any]:
    """Verify Razorpay payment signature and idempotently credit wallet."""
    order = PaymentOrder.objects.filter(provider_order_id=order_id, customer=actor).first()
    if not order:
        reject(404, "NOT_FOUND", "Payment order not found")

    import hashlib
    import hmac

    # Signature verification over order_id|payment_id
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected.lower(), signature.lower()):
        reject(400, "PAYMENT_SIGNATURE_INVALID", "Razorpay payment signature verification failed")

    # Idempotent replay: if already captured with the exact same payment ID, return current state
    if order.state == "CAPTURED":
        if order.provider_payment_id == payment_id:
            wallet, _ = WalletAccount.objects.get_or_create(customer=actor)
            return {
                "status": "CAPTURED",
                "credited_paise": order.amount_paise,
                "credited_inr": round(order.amount_paise / 100.0, 2),
                "balance_paise": wallet.balance_paise,
                "balance_inr": round(wallet.balance_paise / 100.0, 2),
                "currency": wallet.currency,
                "version": wallet.version,
            }
        reject(409, "PAYMENT_CONFLICT", "Order already captured with a different payment ID")

    if order.state == "FAILED":
        reject(400, "PAYMENT_ORDER_FAILED", "Payment order is marked as failed")

    # Atomic credit with row-level lock on wallet
    with transaction.atomic():
        if PaymentOrder.objects.filter(
            provider="RAZORPAY", provider_payment_id=payment_id, state="CAPTURED"
        ).exists():
            reject(409, "DUPLICATE_PAYMENT", "Payment ID has already been credited")

        wallet, _ = WalletAccount.objects.select_for_update().get_or_create(
            customer=actor,
            defaults={"balance_paise": 0, "currency": "INR", "version": 1},
        )
        wallet.balance_paise += order.amount_paise
        wallet.version += 1
        wallet.save(update_fields=["balance_paise", "version", "updated_at"])

        WalletTransaction.objects.create(
            wallet=wallet,
            transaction_type="CREDIT_TOPUP",
            amount_paise=order.amount_paise,
            balance_after_paise=wallet.balance_paise,
            reference_id=payment_id,
            details={"provider_order_id": order_id, "provider": "RAZORPAY"},
        )

        order.state = "CAPTURED"
        order.provider_payment_id = payment_id
        order.provider_signature = signature
        order.save(update_fields=["state", "provider_payment_id", "provider_signature", "updated_at"])

        audit(
            actor,
            wallet,
            "WALLET_RECHARGE_CAPTURED",
            details={
                "amount_paise": order.amount_paise,
                "provider_order_id": order_id,
                "provider_payment_id": payment_id,
                "balance_after_paise": wallet.balance_paise,
            },
        )

    return {
        "status": "CAPTURED",
        "credited_paise": order.amount_paise,
        "credited_inr": round(order.amount_paise / 100.0, 2),
        "balance_paise": wallet.balance_paise,
        "balance_inr": round(wallet.balance_paise / 100.0, 2),
        "currency": wallet.currency,
        "version": wallet.version,
    }

