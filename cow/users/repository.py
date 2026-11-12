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
    CustomerJobRating,
    CustomerNotification,
    CustomerNotificationPreference,
    CustomerProfile,
    CustomerSupportMessage,
    CustomerSupportTicket,
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
from cow.users.schemas import format_paise_to_inr_str

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
    limit = max(1, min(limit, 100))
    qs = ServiceRequest.objects.filter(customer=actor).order_by("-created_at")[:limit]
    return [format_service_request(x) for x in qs]


# ============================================================================
# Service Jobs
# ============================================================================

def get_job_model(actor: Principal, job_id: UUID, *, lock: bool = False) -> ServiceJob:
    if lock:
        # Lock ServiceJob row independently without nullable joins to avoid PostgreSQL outer join lock error
        job = (
            ServiceJob.objects.select_for_update(of=("self",))
            .select_related("request", "accepted_quote")
            .filter(id=job_id, request__customer=actor)
            .first()
        )
    else:
        job = (
            ServiceJob.objects.select_related("request", "accepted_quote", "driver_duty")
            .filter(id=job_id, request__customer=actor)
            .first()
        )
    if not job:
        reject(404, "NOT_FOUND", "Job not found")
    return job


def get_job(actor: Principal, job_id: UUID) -> dict[str, Any]:
    job = get_job_model(actor, job_id)
    return format_job(job)


def list_jobs(actor: Principal, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
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
    session = ChargingSession.objects.filter(job=job).order_by("-started_at").first()
    latest_meter = None
    charging_status = None
    if session:
        obs = MeterObservation.objects.filter(session=session).order_by("-observed_at").first()
        if obs:
            latest_meter = {"energy_wh": obs.energy_wh, "observed_at": obs.observed_at.isoformat()}
        charging_status = {
            "session_id": str(session.id),
            "session_state": session.state,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "meter_start_wh": session.meter_start_wh,
            "last_meter_wh": session.last_meter_wh,
            "latest_meter": latest_meter,
        }
    # Do NOT fabricate ETA: return null unless reliable location/route data is integrated
    driver_location = None
    eta_minutes = None
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
    rating_obj = CustomerJobRating.objects.filter(job=job, customer=actor).first()
    if not rating_obj:
        reject(404, "NOT_FOUND", "No rating found for this job")
    return {
        "job_id": str(job.id),
        "rating": rating_obj.rating,
        "comment": rating_obj.comment or None,
        "version": rating_obj.version,
        "created_at": rating_obj.created_at.isoformat(),
        "updated_at": rating_obj.updated_at.isoformat() if rating_obj.updated_at else None,
    }


def upsert_rating_for_job(
    actor: Principal, job_id: UUID, rating: int, comment: str | None = None, version: int | None = None
) -> dict[str, Any]:
    with transaction.atomic():
        job = get_job_model(actor, job_id)
        if job.state != "COMPLETED":
            reject(409, "INVALID_JOB_STATE", f"Job in state {job.state} cannot be rated; only COMPLETED jobs can be rated")
        clean_comment = comment or ""
        rating_obj = CustomerJobRating.objects.select_for_update().filter(job=job, customer=actor).first()
        if rating_obj:
            if version is None:
                reject(428, "PRECONDITION_REQUIRED", "If-Match header is required to update an existing rating")
            assert_version(rating_obj, version)

            # Idempotent retry: identical submission returns current state without extra version bumps or audits
            if rating_obj.rating == rating and rating_obj.comment == clean_comment:
                return {
                    "job_id": str(job.id),
                    "rating": rating_obj.rating,
                    "comment": rating_obj.comment or None,
                    "version": rating_obj.version,
                    "created_at": rating_obj.created_at.isoformat(),
                    "updated_at": rating_obj.updated_at.isoformat(),
                }

            rating_obj.rating = rating
            rating_obj.comment = clean_comment
            rating_obj.version += 1
            rating_obj.save(update_fields=["rating", "comment", "version", "updated_at"])
            audit(actor, rating_obj, "JOB_RATING_UPDATED", details={"rating": rating, "version": rating_obj.version})
        else:
            rating_obj = CustomerJobRating.objects.create(
                job=job,
                customer=actor,
                rating=rating,
                comment=clean_comment,
                version=1,
            )
            audit(actor, rating_obj, "JOB_RATING_CREATED", details={"rating": rating})
        return {
            "job_id": str(job.id),
            "rating": rating_obj.rating,
            "comment": rating_obj.comment or None,
            "version": rating_obj.version,
            "created_at": rating_obj.created_at.isoformat(),
            "updated_at": rating_obj.updated_at.isoformat(),
        }


def list_ratings(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    ratings = CustomerJobRating.objects.filter(customer=actor).order_by("-updated_at")[:limit]
    return [
        {
            "job_id": str(r.job_id),
            "rating": r.rating,
            "comment": r.comment or None,
            "version": r.version,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in ratings
    ]


# ============================================================================
# Customer Support Tickets
# ============================================================================

def format_support_ticket(ticket: CustomerSupportTicket) -> dict[str, Any]:
    messages = [
        {
            "id": str(m.id),
            "author": "CUSTOMER" if m.sender_id == ticket.customer_id else "SUPPORT",
            "message": m.message,
            "created_at": m.created_at.isoformat(),
        }
        for m in ticket.messages.all().order_by("created_at")
    ]
    return {
        "id": str(ticket.id),
        "channel": "CUSTOMER_PLATFORM",
        "status": ticket.status,
        "category": ticket.category,
        "subject": ticket.subject,
        "job_id": str(ticket.job_id) if ticket.job_id else None,
        "version": ticket.version,
        "messages": messages,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else ticket.created_at.isoformat(),
    }


def create_support_ticket(
    actor: Principal,
    category: str,
    subject: str,
    message: str,
    job_id: UUID | None = None,
) -> dict[str, Any]:
    job = None
    if job_id:
        job = get_job_model(actor, job_id)
    with transaction.atomic():
        ticket = CustomerSupportTicket.objects.create(
            customer=actor,
            job=job,
            subject=subject,
            category=category,
            status="OPEN",
            version=1,
        )
        CustomerSupportMessage.objects.create(
            ticket=ticket,
            sender=actor,
            message=message,
        )
        audit(actor, ticket, "SUPPORT_TICKET_CREATED", details={"category": category, "subject": subject})
        return format_support_ticket(ticket)


def get_support_ticket(actor: Principal, ticket_id: UUID) -> dict[str, Any]:
    ticket = CustomerSupportTicket.objects.prefetch_related("messages").filter(
        id=ticket_id, customer=actor
    ).first()
    if not ticket:
        reject(404, "NOT_FOUND", "Support ticket not found")
    return format_support_ticket(ticket)


def list_support_tickets(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    tickets = (
        CustomerSupportTicket.objects.prefetch_related("messages")
        .filter(customer=actor)
        .order_by("-updated_at")[:limit]
    )
    return [format_support_ticket(t) for t in tickets]


def reply_support_ticket(
    actor: Principal,
    ticket_id: UUID,
    message: str,
    version: int | None = None,
) -> dict[str, Any]:
    with transaction.atomic():
        ticket = (
            CustomerSupportTicket.objects.select_for_update()
            .prefetch_related("messages")
            .filter(id=ticket_id, customer=actor)
            .first()
        )
        if not ticket:
            reject(404, "NOT_FOUND", "Support ticket not found")
        if version is not None:
            assert_version(ticket, version)
        CustomerSupportMessage.objects.create(
            ticket=ticket,
            sender=actor,
            message=message,
        )
        # Customer reply automatically reopens RESOLVED or CLOSED tickets to OPEN
        ticket.status = "OPEN"
        ticket.version += 1
        ticket.save(update_fields=["status", "version", "updated_at"])
        audit(actor, ticket, "SUPPORT_TICKET_REPLIED", details={"version": ticket.version, "status": ticket.status})
        return format_support_ticket(ticket)


# ============================================================================
# Notifications & Preferences
# ============================================================================

def format_notification(n: CustomerNotification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "title": n.title,
        "body": n.message,
        "category": n.notification_type,
        "is_read": n.is_read,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat(),
        "data": n.data or {},
    }


def create_notification(
    customer: Principal,
    title: str,
    message: str,
    notification_type: str = "SERVICE",
    data: dict[str, Any] | None = None,
) -> CustomerNotification | None:
    """Create a notification respecting the customer's delivery preferences."""
    pref, _ = CustomerNotificationPreference.objects.get_or_create(customer=customer)
    if notification_type in ("PROMOTION", "PROMOTIONAL") and not pref.promotions:
        return None
    if notification_type in ("SERVICE", "SERVICE_UPDATE") and not pref.service_updates:
        return None
    return CustomerNotification.objects.create(
        customer=customer,
        title=title,
        message=message,
        notification_type=notification_type,
        data=data or {},
    )


def list_notifications(actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    notifs = CustomerNotification.objects.filter(customer=actor).order_by("-created_at")[:limit]
    return [format_notification(n) for n in notifs]


def get_unread_notification_count(actor: Principal) -> int:
    return CustomerNotification.objects.filter(customer=actor, is_read=False).count()


def mark_notification_read(actor: Principal, notification_id: UUID) -> dict[str, Any]:
    with transaction.atomic():
        notif = CustomerNotification.objects.select_for_update().filter(
            id=notification_id, customer=actor
        ).first()
        if not notif:
            reject(404, "NOT_FOUND", "Notification not found")
        if not notif.is_read:
            notif.is_read = True
            notif.read_at = timezone.now()
            notif.save(update_fields=["is_read", "read_at", "updated_at"])
        return format_notification(notif)


def mark_all_notifications_read(actor: Principal) -> list[dict[str, Any]]:
    now = timezone.now()
    with transaction.atomic():
        unread = list(CustomerNotification.objects.select_for_update().filter(customer=actor, is_read=False))
        if unread:
            CustomerNotification.objects.filter(id__in=[n.id for n in unread]).update(
                is_read=True, read_at=now, updated_at=now
            )
        all_notifs = CustomerNotification.objects.filter(customer=actor).order_by("-created_at")[:50]
        return [format_notification(n) for n in all_notifs]


def get_notification_preferences(actor: Principal) -> dict[str, Any]:
    pref, _ = CustomerNotificationPreference.objects.get_or_create(customer=actor)
    return {
        "push_enabled": pref.push_enabled,
        "email_enabled": pref.email_enabled,
        "sms_enabled": pref.sms_enabled,
        "service_updates": pref.service_updates,
        "promotions": pref.promotions,
    }


def update_notification_preferences(actor: Principal, patch_dict: dict[str, Any]) -> dict[str, Any]:
    with transaction.atomic():
        pref, _ = CustomerNotificationPreference.objects.select_for_update().get_or_create(customer=actor)
        allowed = {"push_enabled", "email_enabled", "sms_enabled", "service_updates", "promotions"}
        fields_to_update = []
        for k, v in patch_dict.items():
            if k in allowed and v is not None:
                setattr(pref, k, v)
                fields_to_update.append(k)
        if fields_to_update:
            fields_to_update.append("updated_at")
            pref.save(update_fields=fields_to_update)
        return {
            "push_enabled": pref.push_enabled,
            "email_enabled": pref.email_enabled,
            "sms_enabled": pref.sms_enabled,
            "service_updates": pref.service_updates,
            "promotions": pref.promotions,
        }


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

    # Realtime SSE streaming transport is not yet commissioned in this backend deployment
    reject(501, "REALTIME_STREAM_NOT_CONFIGURED", "Realtime SSE streaming transport is not commissioned in this deployment")


# ============================================================================
# Wallet & Payment Order Operations
# ============================================================================

def format_wallet(w: WalletAccount) -> dict[str, Any]:
    return {
        "balance_paise": w.balance_paise,
        "balance_inr": format_paise_to_inr_str(w.balance_paise),
        "currency": "INR",
        "version": w.version,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }


def format_wallet_transaction(tx: WalletTransaction) -> dict[str, Any]:
    return {
        "id": str(tx.id),
        "transaction_type": tx.transaction_type,
        "amount_paise": tx.amount_paise,
        "amount_inr": format_paise_to_inr_str(tx.amount_paise),
        "balance_after_paise": tx.balance_after_paise,
        "balance_after_inr": format_paise_to_inr_str(tx.balance_after_paise),
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
    limit = max(1, min(limit, 100))
    wallet, _ = WalletAccount.objects.get_or_create(
        customer=actor,
        defaults={"balance_paise": 0, "currency": "INR", "version": 1},
    )
    txs = WalletTransaction.objects.filter(wallet=wallet).order_by("-created_at")[:limit]
    return [format_wallet_transaction(tx) for tx in txs]


def get_payment_order_by_receipt(actor: Principal, receipt: str, key_id: str = "") -> dict[str, Any] | None:
    """Retrieve an existing payment order by receipt for idempotent order recovery."""
    if not receipt:
        return None
    order = PaymentOrder.objects.filter(customer=actor, receipt=receipt).first()
    if not order:
        return None
    return {
        "order_id": order.provider_order_id,
        "provider_order_id": order.provider_order_id,
        "provider": order.provider,
        "amount_paise": order.amount_paise,
        "amount_inr": format_paise_to_inr_str(order.amount_paise),
        "currency": order.currency,
        "key_id": key_id,
        "state": order.state,
    }


def create_payment_order(
    actor: Principal,
    amount_paise: int,
    currency: str,
    provider_order_id: str,
    key_id: str,
    receipt: str = "",
) -> dict[str, Any]:
    """Record an intent/order for payment top-up."""
    if currency != "INR":
        reject(422, "UNSUPPORTED_CURRENCY", "Only INR currency is supported")
    if not (100 <= amount_paise <= 10000000):
        reject(422, "INVALID_AMOUNT", "Amount must be between 100 paise (₹1) and 10000000 paise (₹100,000)")
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
        "amount_inr": format_paise_to_inr_str(order.amount_paise),
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
    payment_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify payment signature and atomically credit wallet under row-level lock."""
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

    # Authoritative evidence verification is strictly fail-closed: HMAC alone NEVER authorizes credit
    if not payment_evidence or not isinstance(payment_evidence, dict):
        reject(400, "MISSING_PAYMENT_EVIDENCE", "Authoritative payment verification evidence from provider is required")

    # Reject simulation in real payment flows
    if payment_evidence.get("simulated"):
        reject(400, "PAYMENT_SIMULATION_DISALLOWED", "Simulated payments are not permitted to credit wallet ledgers")

    with transaction.atomic():
        # Lock PaymentOrder inside transaction FIRST
        order = PaymentOrder.objects.select_for_update().filter(
            provider_order_id=order_id, customer=actor
        ).first()
        if not order:
            reject(404, "NOT_FOUND", "Payment order not found")

        # Idempotent replay: if already captured with the exact same payment ID, return current state
        if order.state == "CAPTURED":
            if order.provider_payment_id == payment_id:
                wallet, _ = WalletAccount.objects.get_or_create(customer=actor)
                return {
                    "status": "CAPTURED",
                    "credited_paise": order.amount_paise,
                    "credited_inr": format_paise_to_inr_str(order.amount_paise),
                    "balance_paise": wallet.balance_paise,
                    "balance_inr": format_paise_to_inr_str(wallet.balance_paise),
                    "currency": wallet.currency,
                    "version": wallet.version,
                }
            reject(409, "PAYMENT_CONFLICT", "Order already captured with a different payment ID")

        if order.state != "PENDING":
            reject(400, "INVALID_ORDER_STATE", f"Payment order is in state {order.state}")

        # Strict fail-closed verification of authoritative evidence against order and submitted credentials
        ev_status = payment_evidence.get("status")
        if ev_status != "captured":
            reject(400, "PAYMENT_NOT_CAPTURED", f"Payment status is '{ev_status}', not 'captured'")

        ev_id = payment_evidence.get("id")
        if not ev_id or ev_id != payment_id:
            reject(400, "PAYMENT_ID_MISMATCH", f"Payment evidence ID '{ev_id}' does not match submitted payment '{payment_id}'")

        ev_order_id = payment_evidence.get("order_id")
        if not ev_order_id or ev_order_id != order.provider_order_id:
            reject(400, "PAYMENT_ORDER_MISMATCH", f"Payment evidence order ID '{ev_order_id}' does not match order '{order.provider_order_id}'")

        ev_amount = payment_evidence.get("amount")
        if ev_amount is None or ev_amount != order.amount_paise:
            reject(400, "PAYMENT_AMOUNT_MISMATCH", f"Payment amount {ev_amount} does not match order {order.amount_paise}")

        ev_currency = payment_evidence.get("currency")
        if not ev_currency or ev_currency != order.currency:
            reject(400, "PAYMENT_CURRENCY_MISMATCH", f"Payment currency '{ev_currency}' does not match order '{order.currency}'")

        # Check if this payment_id has already been credited across any order
        if PaymentOrder.objects.filter(
            provider="RAZORPAY", provider_payment_id=payment_id, state="CAPTURED"
        ).exists():
            reject(409, "DUPLICATE_PAYMENT", "Payment ID has already been credited")

        # Strip any sensitive credentials before persisting durable evidence
        safe_evidence = {
            k: v for k, v in payment_evidence.items()
            if k not in {"secret", "key_secret", "auth", "Authorization"}
        }

        # Lock the customer's wallet before updating balance
        wallet, _ = WalletAccount.objects.select_for_update().get_or_create(
            customer=actor,
            defaults={"balance_paise": 0, "currency": "INR", "version": 1},
        )
        wallet.balance_paise += order.amount_paise
        wallet.version += 1
        wallet.save(update_fields=["balance_paise", "version", "updated_at"])

        # Append immutable ledger transaction
        WalletTransaction.objects.create(
            wallet=wallet,
            transaction_type="CREDIT_TOPUP",
            amount_paise=order.amount_paise,
            balance_after_paise=wallet.balance_paise,
            reference_id=payment_id,
            details=safe_evidence,
        )

        # Transition order to CAPTURED
        order.state = "CAPTURED"
        order.provider_payment_id = payment_id
        order.provider_signature = signature
        order.details = safe_evidence
        order.save(update_fields=["state", "provider_payment_id", "provider_signature", "details", "updated_at"])

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
        "credited_inr": format_paise_to_inr_str(order.amount_paise),
        "balance_paise": wallet.balance_paise,
        "balance_inr": format_paise_to_inr_str(wallet.balance_paise),
        "currency": wallet.currency,
        "version": wallet.version,
    }


