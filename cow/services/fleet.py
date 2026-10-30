"""Transactional Admin service-vehicle operations.

This module owns inventory truth only. It deliberately never infers charger
HAL health or dispatch eligibility from master-data records.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from cow.errors import reject
from cow.models import (
    AuditEvent,
    DutySession,
    ServiceVehicle,
    VehicleChargerPairing,
    VehicleMaintenancePeriod,
)


def vehicle_data(vehicle: ServiceVehicle) -> dict[str, Any]:
    return {
        "id": str(vehicle.id),
        "asset_tag": vehicle.asset_tag,
        "registration_number": vehicle.registration_number,
        "make": vehicle.make,
        "model": vehicle.model,
        "variant": vehicle.variant,
        "vehicle_type": vehicle.vehicle_type,
        "ownership_type": vehicle.ownership_type,
        "registration_valid_until": _date(vehicle.registration_valid_until),
        "insurance_valid_until": _date(vehicle.insurance_valid_until),
        "inspection_status": vehicle.inspection_status,
        "operational_status": vehicle.operational_status,
        "maintenance_status": vehicle.maintenance_status,
        "is_active": vehicle.active,
        "notes": vehicle.notes,
        "created_at": vehicle.created_at.isoformat(),
        "updated_at": vehicle.updated_at.isoformat(),
        "version": vehicle.version,
    }


def maintenance_data(period: VehicleMaintenancePeriod) -> dict[str, Any]:
    return {
        "id": str(period.id),
        "vehicle_id": str(period.vehicle_id),
        "starts_at": period.starts_at.isoformat(),
        "ends_at": period.ends_at.isoformat(),
        "reason": period.reason,
        "created_by": str(period.created_by_id) if period.created_by_id else None,
        "created_at": period.created_at.isoformat(),
    }


def _date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _audit(
    actor,
    vehicle: ServiceVehicle,
    action: str,
    *,
    reason: str = "",
    before=None,
    after=None,
    extra=None,
) -> None:
    details = {"reason": reason, "before": before, "after": after}
    if extra:
        details.update(extra)
    AuditEvent.objects.create(
        actor=actor,
        target_type="ServiceVehicle",
        target_id=vehicle.id,
        action=action,
        details=details,
    )


def create(actor, fields: dict[str, Any]) -> ServiceVehicle:
    with transaction.atomic():
        vehicle = ServiceVehicle.objects.create(**fields)
        _audit(actor, vehicle, "CREATE", after=vehicle_data(vehicle))
        return vehicle


def get(vehicle_id, *, lock: bool = False) -> ServiceVehicle:
    objects = ServiceVehicle.objects.select_for_update() if lock else ServiceVehicle.objects
    vehicle = objects.filter(id=vehicle_id).first()
    if not vehicle:
        reject(404, "NOT_FOUND", "Asset not found")
    return vehicle


def patch(actor, vehicle_id, version: int, fields: dict[str, Any]) -> ServiceVehicle:
    with transaction.atomic():
        vehicle = get(vehicle_id, lock=True)
        if vehicle.version != version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Asset version changed; reload and retry")
        before = vehicle_data(vehicle)
        changed = False
        for name, value in fields.items():
            if getattr(vehicle, name) != value:
                setattr(vehicle, name, value)
                changed = True
        if not changed:
            return vehicle
        vehicle.version += 1
        vehicle.save()
        _audit(actor, vehicle, "UPDATE", before=before, after=vehicle_data(vehicle))
        return vehicle


def set_active(actor, vehicle_id, version: int, reason: str, active: bool) -> ServiceVehicle:
    with transaction.atomic():
        vehicle = get(vehicle_id, lock=True)
        if vehicle.version != version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Asset version changed; reload and retry")
        if vehicle.operational_status == "RETIRED":
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        if vehicle.active == active:
            return vehicle
        if not active:
            paired = VehicleChargerPairing.objects.filter(
                vehicle=vehicle, effective_to__isnull=True
            ).exists()
            claimed = DutySession.objects.filter(vehicle=vehicle, ended_at__isnull=True).exists()
            if paired or claimed:
                reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        elif VehicleMaintenancePeriod.objects.filter(
            vehicle=vehicle, starts_at__lte=timezone.now(), ends_at__gt=timezone.now()
        ).exists():
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        before = vehicle_data(vehicle)
        vehicle.active = active
        vehicle.operational_status = "AVAILABLE" if active else "OUT_OF_SERVICE"
        vehicle.version += 1
        vehicle.save()
        _audit(
            actor,
            vehicle,
            "REACTIVATE" if active else "DEACTIVATE",
            reason=reason,
            before=before,
            after=vehicle_data(vehicle),
        )
        return vehicle


def add_maintenance(actor, vehicle_id, starts_at, ends_at, reason: str) -> VehicleMaintenancePeriod:
    # The parent lock makes the application path deterministic; 0003's trigger
    # independently enforces the same no-overlap invariant for all DB writers.
    with transaction.atomic():
        vehicle = get(vehicle_id, lock=True)
        if VehicleMaintenancePeriod.objects.filter(
            vehicle=vehicle, starts_at__lt=ends_at, ends_at__gt=starts_at
        ).exists():
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        period = VehicleMaintenancePeriod.objects.create(
            vehicle=vehicle,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
            created_by=actor,
        )
        _audit(
            actor,
            vehicle,
            "MAINTENANCE",
            reason=reason,
            extra={"maintenance": maintenance_data(period)},
        )
        return period


def availability(vehicle_id) -> dict[str, Any]:
    vehicle = get(vehicle_id)
    now = timezone.now()
    maintenance = VehicleMaintenancePeriod.objects.filter(
        vehicle=vehicle, starts_at__lte=now, ends_at__gt=now
    ).exists()
    pairings = VehicleChargerPairing.objects.filter(
        vehicle=vehicle, effective_to__isnull=True
    ).count()
    inventory_state = vehicle.operational_status
    if maintenance:
        inventory_state = "MAINTENANCE"
    elif not vehicle.active and inventory_state != "RETIRED":
        inventory_state = "OUT_OF_SERVICE"
    return {
        "asset_id": str(vehicle.id),
        "inventory_state": inventory_state,
        "active_pairings": pairings,
        "maintenance_now": maintenance,
        "dispatch_eligibility": "NOT_EVALUATED",
        "hal_connectivity": "NOT_INTEGRATED",
    }


def history(vehicle_id) -> list[dict[str, Any]]:
    get(vehicle_id)
    return [
        {
            "id": str(event.id),
            "actor_id": str(event.actor_id) if event.actor_id else None,
            "asset_kind": "VEHICLE",
            "asset_id": str(event.target_id) if event.target_id else None,
            "action": event.action,
            "reason": event.details.get("reason", ""),
            "before_value": event.details.get("before"),
            "after_value": event.details.get("after"),
            "details": event.details,
            "created_at": event.occurred_at.isoformat(),
        }
        for event in AuditEvent.objects.filter(
            target_type="ServiceVehicle", target_id=vehicle_id
        ).order_by("-occurred_at", "-id")[:100]
    ]


def list_vehicles(
    *, limit: int, after, status: str, active: bool | None, query: str
) -> tuple[list[ServiceVehicle], str | None]:
    vehicles = ServiceVehicle.objects.all()
    if after:
        cursor = ServiceVehicle.objects.filter(id=after).values("created_at", "id").first()
        if not cursor:
            reject(422, "VALIDATION_FAILED", "Invalid fleet request")
        vehicles = vehicles.filter(
            Q(created_at__gt=cursor["created_at"])
            | Q(created_at=cursor["created_at"], id__gt=cursor["id"])
        )
    if status:
        vehicles = vehicles.filter(operational_status=status)
    if active is not None:
        vehicles = vehicles.filter(active=active)
    if query:
        vehicles = vehicles.filter(Q(asset_tag__icontains=query) | Q(model__icontains=query))
    page = list(vehicles.order_by("created_at", "id")[: limit + 1])
    next_cursor = str(page[limit - 1].id) if len(page) > limit else None
    return page[:limit], next_cursor
