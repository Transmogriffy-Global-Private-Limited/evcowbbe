"""Transactional Admin service-vehicle operations.

This module owns inventory truth only. It deliberately never infers charger
HAL health or dispatch eligibility from master-data records.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from cow.errors import reject
from cow.models import (
    AuditEvent,
    Charger,
    ChargerConnector,
    ChargerMaintenancePeriod,
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


def charger_data(charger: Charger) -> dict[str, Any]:
    return {
        "id": str(charger.id),
        "asset_tag": charger.asset_tag,
        "serial_number": charger.serial_number,
        "manufacturer": charger.manufacturer,
        "model": charger.model,
        "rated_power_w": charger.rated_power_w,
        "protocol_family": charger.protocol_family,
        "protocol_version": charger.protocol_version,
        "hal_identity": charger.hal_identity,
        "operational_status": charger.operational_status,
        "maintenance_status": charger.maintenance_status,
        "is_active": charger.active,
        "notes": charger.notes,
        "created_at": charger.created_at.isoformat(),
        "updated_at": charger.updated_at.isoformat(),
        "version": charger.version,
    }


def connector_data(connector: ChargerConnector) -> dict[str, Any]:
    return {
        "id": str(connector.id),
        "charger_id": str(connector.charger_id),
        "connector_number": connector.connector_number,
        "connector_type": connector.connector_type,
        "max_power_w": connector.max_power_w,
        "supports_ac": connector.supports_ac,
        "supports_dc": connector.supports_dc,
        "is_active": connector.active,
        "created_at": connector.created_at.isoformat(),
        "updated_at": connector.updated_at.isoformat(),
        "version": connector.version,
    }


def charger_maintenance_data(period: ChargerMaintenancePeriod) -> dict[str, Any]:
    return {
        "id": str(period.id),
        "charger_id": str(period.charger_id),
        "starts_at": period.starts_at.isoformat(),
        "ends_at": period.ends_at.isoformat(),
        "reason": period.reason,
        "created_by": str(period.created_by_id) if period.created_by_id else None,
        "created_at": period.created_at.isoformat(),
    }


def pairing_data(pairing: VehicleChargerPairing) -> dict[str, Any]:
    return {
        "id": str(pairing.id),
        "vehicle_id": str(pairing.vehicle_id),
        "charger_id": str(pairing.charger_id),
        "effective_from": pairing.effective_from.isoformat(),
        "effective_to": pairing.effective_to.isoformat() if pairing.effective_to else None,
        "status": pairing.status,
        "source": pairing.source,
        "created_by": str(pairing.created_by_id) if pairing.created_by_id else None,
        "created_at": pairing.created_at.isoformat(),
        "reason": pairing.reason,
        "end_reason": pairing.end_reason,
        "ended_by": str(pairing.ended_by_id) if pairing.ended_by_id else None,
    }


def _audit_asset(actor, target_type: str, target_id, action: str, **details) -> None:
    AuditEvent.objects.create(
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        action=action,
        details=details,
    )


def get_charger(charger_id, *, lock: bool = False) -> Charger:
    objects = Charger.objects.select_for_update() if lock else Charger.objects
    charger = objects.filter(id=charger_id).first()
    if not charger:
        reject(404, "NOT_FOUND", "Asset not found")
    return charger


def list_chargers(
    *, limit: int, after, status: str, active: bool | None, query: str
) -> tuple[list[Charger], str | None]:
    chargers = Charger.objects.all()
    if after:
        cursor = Charger.objects.filter(id=after).values("created_at", "id").first()
        if not cursor:
            reject(422, "VALIDATION_FAILED", "Invalid fleet request")
        chargers = chargers.filter(
            Q(created_at__gt=cursor["created_at"])
            | Q(created_at=cursor["created_at"], id__gt=cursor["id"])
        )
    if status:
        chargers = chargers.filter(operational_status=status)
    if active is not None:
        chargers = chargers.filter(active=active)
    if query:
        chargers = chargers.filter(Q(asset_tag__icontains=query) | Q(model__icontains=query))
    page = list(chargers.order_by("created_at", "id")[: limit + 1])
    next_cursor = str(page[limit - 1].id) if len(page) > limit else None
    return page[:limit], next_cursor


def create_charger(actor, fields: dict[str, Any]) -> Charger:
    with transaction.atomic():
        charger = Charger.objects.create(**fields)
        _audit_asset(actor, "Charger", charger.id, "CREATE", after=charger_data(charger))
        return charger


def patch_charger(actor, charger_id, version: int, fields: dict[str, Any]) -> Charger:
    with transaction.atomic():
        charger = get_charger(charger_id, lock=True)
        if charger.version != version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Asset version changed; reload and retry")
        before = charger_data(charger)
        changed = False
        for name, value in fields.items():
            if getattr(charger, name) != value:
                setattr(charger, name, value)
                changed = True
        if not changed:
            return charger
        charger.version += 1
        charger.save()
        _audit_asset(
            actor, "Charger", charger.id, "UPDATE", before=before, after=charger_data(charger)
        )
        return charger


def set_charger_active(actor, charger_id, version: int, reason: str, active: bool) -> Charger:
    with transaction.atomic():
        charger = get_charger(charger_id, lock=True)
        if charger.version != version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Asset version changed; reload and retry")
        if charger.operational_status == "RETIRED":
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        if charger.active == active:
            return charger
        if not active:
            if VehicleChargerPairing.objects.filter(charger=charger, status="ACTIVE").exists():
                reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        elif ChargerMaintenancePeriod.objects.filter(
            charger=charger, starts_at__lte=timezone.now(), ends_at__gt=timezone.now()
        ).exists():
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        before = charger_data(charger)
        charger.active = active
        charger.operational_status = "AVAILABLE" if active else "OUT_OF_SERVICE"
        charger.version += 1
        charger.save()
        _audit_asset(
            actor,
            "Charger",
            charger.id,
            "REACTIVATE" if active else "DEACTIVATE",
            reason=reason,
            before=before,
            after=charger_data(charger),
        )
        return charger


def add_charger_maintenance(
    actor, charger_id, starts_at, ends_at, reason: str
) -> ChargerMaintenancePeriod:
    with transaction.atomic():
        charger = get_charger(charger_id, lock=True)
        if charger.operational_status == "RETIRED":
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        if ChargerMaintenancePeriod.objects.filter(
            charger=charger, starts_at__lt=ends_at, ends_at__gt=starts_at
        ).exists():
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        period = ChargerMaintenancePeriod.objects.create(
            charger=charger,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
            created_by=actor,
        )
        _audit_asset(
            actor,
            "ChargerMaintenancePeriod",
            period.id,
            "MAINTENANCE",
            reason=reason,
            maintenance=charger_maintenance_data(period),
        )
        return period


def charger_availability(charger_id) -> dict[str, Any]:
    charger = get_charger(charger_id)
    now = timezone.now()
    maintenance = ChargerMaintenancePeriod.objects.filter(
        charger=charger, starts_at__lte=now, ends_at__gt=now
    ).exists()
    pairings = VehicleChargerPairing.objects.filter(charger=charger, status="ACTIVE").count()
    inventory_state = charger.operational_status
    if maintenance:
        inventory_state = "MAINTENANCE"
    elif not charger.active and inventory_state != "RETIRED":
        inventory_state = "OUT_OF_SERVICE"
    return {
        "asset_id": str(charger.id),
        "inventory_state": inventory_state,
        "active_pairings": pairings,
        "maintenance_now": maintenance,
        "dispatch_eligibility": "NOT_EVALUATED",
        "hal_connectivity": "NOT_INTEGRATED",
    }


def list_connectors(charger_id) -> list[ChargerConnector]:
    get_charger(charger_id)
    return list(
        ChargerConnector.objects.filter(charger_id=charger_id).order_by("connector_number")[:100]
    )


def create_connector(actor, charger_id, fields: dict[str, Any]) -> ChargerConnector:
    with transaction.atomic():
        charger = get_charger(charger_id, lock=True)
        if not charger.active or charger.operational_status != "AVAILABLE":
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        if not fields["supports_ac"] and not fields["supports_dc"]:
            reject(422, "VALIDATION_FAILED", "Invalid fleet request")
        connector = ChargerConnector.objects.create(charger=charger, **fields)
        _audit_asset(
            actor,
            "ChargerConnector",
            connector.id,
            "CREATE",
            after=connector_data(connector),
        )
        return connector


def patch_connector(
    actor, charger_id, connector_id, version: int, fields: dict[str, Any]
) -> ChargerConnector:
    with transaction.atomic():
        charger = get_charger(charger_id, lock=True)
        if not charger.active:
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        connector = (
            ChargerConnector.objects.select_for_update()
            .filter(id=connector_id, charger=charger)
            .first()
        )
        if not connector:
            reject(404, "NOT_FOUND", "Asset not found")
        if connector.version != version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Asset version changed; reload and retry")
        next_ac = fields.get("supports_ac", connector.supports_ac)
        next_dc = fields.get("supports_dc", connector.supports_dc)
        if not next_ac and not next_dc:
            reject(422, "VALIDATION_FAILED", "Invalid fleet request")
        before = connector_data(connector)
        changed = False
        for name, value in fields.items():
            if getattr(connector, name) != value:
                setattr(connector, name, value)
                changed = True
        if not changed:
            return connector
        connector.version += 1
        connector.save()
        _audit_asset(
            actor,
            "ChargerConnector",
            connector.id,
            "UPDATE",
            before=before,
            after=connector_data(connector),
        )
        return connector


def list_pairings(
    *, vehicle_id, charger_id, status: str, limit: int
) -> list[VehicleChargerPairing]:
    pairings = VehicleChargerPairing.objects.all()
    if vehicle_id:
        pairings = pairings.filter(vehicle_id=vehicle_id)
    if charger_id:
        pairings = pairings.filter(charger_id=charger_id)
    if status:
        pairings = pairings.filter(status=status)
    return list(pairings.order_by("-effective_from", "-id")[:limit])


def create_pairing(actor, vehicle_id, charger_id, reason: str) -> VehicleChargerPairing:
    with transaction.atomic():
        # The global lock order must match end_pairing and future duty claims.
        vehicle = get(vehicle_id, lock=True)
        charger = get_charger(charger_id, lock=True)
        if (
            not vehicle.active
            or vehicle.operational_status != "AVAILABLE"
            or not charger.active
            or charger.operational_status != "AVAILABLE"
        ):
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        now = timezone.now()
        if (
            VehicleMaintenancePeriod.objects.filter(
                vehicle=vehicle, starts_at__lte=now, ends_at__gt=now
            ).exists()
            or ChargerMaintenancePeriod.objects.filter(
                charger=charger, starts_at__lte=now, ends_at__gt=now
            ).exists()
        ):
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        pairing = VehicleChargerPairing.objects.create(
            vehicle=vehicle,
            charger=charger,
            effective_from=now,
            status="ACTIVE",
            source="ADMIN",
            created_by=actor,
            reason=reason,
        )
        _audit_asset(
            actor,
            "VehicleChargerPairing",
            pairing.id,
            "PAIR",
            reason=reason,
            after=pairing_data(pairing),
        )
        return pairing


def end_pairing(actor, pairing_id, reason: str) -> VehicleChargerPairing:
    with transaction.atomic():
        pairing = (
            VehicleChargerPairing.objects.select_related("vehicle", "charger")
            .filter(id=pairing_id)
            .first()
        )
        if not pairing:
            reject(404, "NOT_FOUND", "Asset not found")
        # Lock the same two master resources before the pairing row.
        get(pairing.vehicle_id, lock=True)
        get_charger(pairing.charger_id, lock=True)
        pairing = VehicleChargerPairing.objects.select_for_update().get(id=pairing_id)
        if pairing.status != "ACTIVE":
            reject(409, "CONFLICT", "Fleet state conflicts with this operation")
        before = pairing_data(pairing)
        pairing.status = "ENDED"
        pairing.effective_to = max(
            timezone.now(), pairing.effective_from + timedelta(microseconds=1)
        )
        pairing.ended_by = actor
        pairing.end_reason = reason
        pairing.save(update_fields=["status", "effective_to", "ended_by", "end_reason"])
        _audit_asset(
            actor,
            "VehicleChargerPairing",
            pairing.id,
            "END",
            reason=reason,
            before=before,
            after=pairing_data(pairing),
        )
        return pairing


def charger_history(charger_id) -> list[dict[str, Any]]:
    get_charger(charger_id)
    pairing_ids = VehicleChargerPairing.objects.filter(charger_id=charger_id).values("id")
    maintenance_ids = ChargerMaintenancePeriod.objects.filter(charger_id=charger_id).values("id")
    events = AuditEvent.objects.filter(
        Q(target_type="Charger", target_id=charger_id)
        | Q(target_type="VehicleChargerPairing", target_id__in=pairing_ids)
        | Q(target_type="ChargerMaintenancePeriod", target_id__in=maintenance_ids)
    ).order_by("-occurred_at", "-id")[:100]
    kinds = {
        "Charger": "CHARGER",
        "VehicleChargerPairing": "PAIRING",
        "ChargerMaintenancePeriod": "MAINTENANCE",
    }
    return [
        {
            "id": str(event.id),
            "actor_id": str(event.actor_id) if event.actor_id else None,
            "asset_kind": kinds[event.target_type],
            "asset_id": str(event.target_id) if event.target_id else None,
            "action": event.action,
            "reason": event.details.get("reason", ""),
            "before_value": event.details.get("before"),
            "after_value": event.details.get("after"),
            "details": event.details,
            "created_at": event.occurred_at.isoformat(),
        }
        for event in events
    ]
