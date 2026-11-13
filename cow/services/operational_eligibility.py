"""Stage B safety admission shared by Admin/fleet and the opt-in HAL transport.

This is a narrow safety boundary, NOT driver onboarding, dispatch acceptance,
commercial quotation, charger connectivity, or permission to energize hardware.

Every participating writer must acquire the transaction gate BEFORE locking
operational resources. Admin writers acquire the RBAC global lock first.
The gate serializes admission, fleet mutations, HAL send-intent persistence,
and fact application; a future duty creation/suspension implementation MUST
join it. It does not protect direct SQL writers or external HAL behavior.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import connection
from django.utils import timezone

from cow.common.errors import reject
from cow.models import (
    Charger, ChargerConnector, ChargerMaintenancePeriod, ChargingSession,
    DriverProfile, DutySession, HALCommand, ServiceJob, ServiceVehicle,
    VehicleChargerPairing, VehicleMaintenancePeriod,
)

# Stable PostgreSQL advisory key. One coarse safety gate is deliberate during
# the pre-duty-API phase. Replace with ordered per-aggregate keys only after
# every affected writer and multi-process race test is migrated together.
_GATE = int.from_bytes(
    hashlib.sha256(b"cow-operational-admission-v1").digest()[:8],
    byteorder="big", signed=True,
)
BUSY_COMMAND_STATES = frozenset({
    "PENDING", "DELIVERY_ATTEMPTED", "HAL_ACCEPTED", "OUTCOME_UNKNOWN",
})


def lock_gate() -> None:
    if not connection.in_atomic_block:
        raise RuntimeError("Operational gate requires transaction.atomic()")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_GATE])


def fresh_admin(actor, capability: str):
    """Run INSIDE atomic(), and BEFORE lock_gate() or any asset row lock."""
    if not connection.in_atomic_block:
        raise RuntimeError("Admin authorization requires transaction.atomic()")
    from cow.services.admin_rbac import _lock_global, _require_locked_actor
    _lock_global()
    return _require_locked_actor(actor, capability)


def _require_gate():
    # The gate itself cannot be introspected reliably from pg_locks at each
    # call. This private predicate enforces the mandatory transaction scope;
    # callers are documented and integration-tested for lock ordering.
    if not connection.in_atomic_block:
        raise RuntimeError("Operational admission requires transaction.atomic()")


def _blocking_activity(*, vehicle_id=None, charger_id=None, include_pairing=False):
    """Read-only, non-PII reasons. Called under the operational gate."""
    _require_gate()
    blockers = []
    duties = DutySession.objects.filter(ended_at__isnull=True)
    if vehicle_id is not None and duties.filter(vehicle_id=vehicle_id).exists():
        blockers.append("OPEN_VEHICLE_DUTY")
    if charger_id is not None and duties.filter(charger_id=charger_id).exists():
        blockers.append("OPEN_CHARGER_DUTY")
    if charger_id is not None:
        if ChargingSession.objects.filter(charger_id=charger_id, state="ACTIVE").exists():
            blockers.append("ACTIVE_CHARGING_SESSION")
        if HALCommand.objects.filter(charger_id=charger_id, state__in=BUSY_COMMAND_STATES).exists():
            blockers.append("UNRESOLVED_HAL_COMMAND")
    if vehicle_id is not None:
        if ChargingSession.objects.filter(
            job__driver_duty__vehicle_id=vehicle_id, state="ACTIVE"
        ).exists():
            blockers.append("ACTIVE_VEHICLE_CHARGING_SESSION")
        if HALCommand.objects.filter(
            job__driver_duty__vehicle_id=vehicle_id, state__in=BUSY_COMMAND_STATES
        ).exists():
            blockers.append("UNRESOLVED_VEHICLE_HAL_COMMAND")
    if include_pairing and vehicle_id is not None and VehicleChargerPairing.objects.filter(
        vehicle_id=vehicle_id, status="ACTIVE", effective_to__isnull=True,
    ).exists():
        blockers.append("ACTIVE_VEHICLE_PAIRING")
    if include_pairing and charger_id is not None and VehicleChargerPairing.objects.filter(
        charger_id=charger_id, status="ACTIVE", effective_to__isnull=True,
    ).exists():
        blockers.append("ACTIVE_CHARGER_PAIRING")
    return blockers


def ensure_quiescent(*, vehicle_id=None, charger_id=None, include_pairing=False):
    lock_gate()  # Also enforce at the shared service boundary.
    blockers = _blocking_activity(
        vehicle_id=vehicle_id, charger_id=charger_id, include_pairing=include_pairing,
    )
    if blockers:
        reject(409, "ASSET_OPERATION_BLOCKED", ", ".join(blockers))


def _in_maintenance(model, owner_id_name: str, owner_id, instant: datetime) -> bool:
    return model.objects.filter(**{
        owner_id_name: owner_id,
        "starts_at__lte": instant,
        "ends_at__gt": instant,
    }).exists()


def assert_start_admissible(job_id, charger_id, connector_number: int, *,
                            existing_command_id=None) -> ServiceJob:
    """Validate current operational facts AFTER lock_gate(), before command IO.

    Existing command is excluded only during a second, mandatory pre-send
    assessment. The Admin API must pass None and refuse concurrent intents.
    """
    _require_gate()
    lock_gate()  # Fail closed even when a future caller forgets the gate.
    job = ServiceJob.objects.select_for_update().filter(pk=job_id).first()
    if job is None:
        reject(404, "NOT_FOUND", "Job not found")
    if job.state != "SERVICE_PREPARING":
        reject(409, "JOB_STATE_CONFLICT", "Job not in physical preparation")
    if job.driver_duty_id is None:
        reject(409, "DUTY_NOT_ACTIVE", "Assigned operational duty required")
    duty = DutySession.objects.select_for_update().filter(pk=job.driver_duty_id).first()
    if (duty is None or duty.ended_at is not None or duty.state != "SERVICING"
        or duty.started_at > timezone.now()):
        reject(409, "DUTY_NOT_ACTIVE", "Driver duty must be actively servicing")
    if duty.charger_id != charger_id:
        reject(422, "DUTY_CHARGER_MISMATCH", "Duty does not own requested charger")

    driver = DriverProfile.objects.select_for_update().select_related(
        "principal", "principal__account"
    ).filter(pk=duty.driver_id).first()
    if driver is None:
        reject(409, "DRIVER_NOT_ELIGIBLE", "Driver profile missing")
    # Stored ACTIVE and licence date are insufficient: C1 evidence must still
    # be currently valid. This is a pure DB guard under the Stage B gate.
    from cow.services import driver_operational
    if driver_operational.effective_issues(driver):
        reject(409, "DRIVER_NOT_ELIGIBLE", "Driver operational clearance is not current")

    vehicle = ServiceVehicle.objects.select_for_update().filter(pk=duty.vehicle_id).first()
    charger = Charger.objects.select_for_update().filter(pk=charger_id).first()
    if vehicle is None or charger is None:
        reject(409, "ASSET_NOT_OPERATIONAL", "Assigned physical asset missing")
    assert_duty_assets(vehicle, charger, timezone.now(), connector_number=connector_number)
    from cow.models import DriverAssetMismatch
    if DriverAssetMismatch.objects.filter(duty=duty, status="UNRESOLVED").exists():
        reject(409, "ASSET_MISMATCH_UNRESOLVED", "Reported asset mismatch blocks charging")
    # A duty must not service two concurrent live jobs.
    if ServiceJob.objects.filter(driver_duty_id=duty.pk, state__in=(
        "ASSIGNED", "ACCEPTED", "EN_ROUTE", "ARRIVED", "SERVICE_PREPARING",
        "CHARGING", "WRAP_UP", "REASSIGNING",
    )).exclude(pk=job.pk).exists():
        reject(409, "DUTY_HAS_ACTIVE_JOB", "Duty already services another job")
    if ChargingSession.objects.filter(job_id=job.pk).exclude(state="ENDED").exists():
        reject(409, "CHARGING_SESSION_CONFLICT", "Job already has an active physical session")
    if ChargingSession.objects.filter(
        charger_id=charger_id, connector_number=connector_number, state="ACTIVE"
    ).exists():
        reject(409, "CONNECTOR_IN_USE", "Physical connector is occupied")
    pending = HALCommand.objects.filter(
        job_id=job.pk, action="StartCharging", state__in=BUSY_COMMAND_STATES
    )
    if existing_command_id is not None:
        pending = pending.exclude(pk=existing_command_id)
    if pending.exists():
        reject(409, "CHARGING_COMMAND_CONFLICT", "Previous charging command unresolved")
    return job


def assert_duty_assets(vehicle: ServiceVehicle, charger: Charger, now: datetime, *,
                       connector_number: int | None = None) -> None:
    """One current physical eligibility rule for C3 duty and Stage B charging.

    Call inside transaction.atomic(), after the global operational admission
    gate and after locking the vehicle then charger. This grants no HAL action.
    """
    _require_gate()
    if (not vehicle.active or vehicle.operational_status != "AVAILABLE"
        or not charger.active or charger.operational_status != "AVAILABLE"):
        reject(409, "ASSET_NOT_OPERATIONAL", "Assigned asset is unavailable")
    today = timezone.localdate(timezone=ZoneInfo("Asia/Kolkata"))
    if (vehicle.inspection_status != "VALID"
        or vehicle.registration_valid_until is None or vehicle.registration_valid_until < today
        or vehicle.insurance_valid_until is None or vehicle.insurance_valid_until < today):
        reject(409, "ASSET_NOT_OPERATIONAL", "Vehicle certificates or inspection not verified")
    if not charger.hal_identity or not charger.rated_power_w:
        reject(409, "CHARGER_HAL_IDENTITY_UNKNOWN", "Charger lacks required identity/power")
    if connector_number is not None:
        connector = ChargerConnector.objects.select_for_update().filter(
            charger_id=charger.pk, connector_number=connector_number, active=True
        ).first()
        if connector is None:
            reject(409, "CONNECTOR_NOT_OPERATIONAL", "Connector is unregistered or inactive")
    if (_in_maintenance(VehicleMaintenancePeriod, "vehicle_id", vehicle.pk, now)
        or _in_maintenance(ChargerMaintenancePeriod, "charger_id", charger.pk, now)):
        reject(409, "ASSET_IN_MAINTENANCE", "Asset is under maintenance")
    if not VehicleChargerPairing.objects.filter(
        vehicle_id=vehicle.pk, charger_id=charger.pk, status="ACTIVE",
        effective_from__lte=now, effective_to__isnull=True,
    ).exists():
        reject(409, "ASSET_PAIRING_MISMATCH_BLOCKED", "Effective pairing not proven")
    # A reported physical identity mismatch is an asset-level safety hold,
    # including AFTER the original duty ends. C3.1 has no approved resolution
    # operation; a new duty must not silently forget the prior unsafe report.
    from django.db.models import Q
    from cow.models import DriverAssetMismatch
    if DriverAssetMismatch.objects.filter(status="UNRESOLVED").filter(
        Q(expected_vehicle_id=vehicle.pk) | Q(expected_charger_id=charger.pk)
    ).exists():
        reject(409, "ASSET_MISMATCH_UNRESOLVED",
               "Unreconciled physical asset report blocks admission")
