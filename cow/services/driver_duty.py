"""C3 driver schedule, duty and exclusive asset-claim operations.

Scheduling is administrative intent. A live DutySession is the only exclusive
claim and the only operational state. No separate online boolean is trusted.
All mutations join the C2/Stage-B operational advisory gate. No HAL I/O.
"""
from __future__ import annotations

import unicodedata
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from cow.common.errors import reject
from cow.models import (
    AuditEvent, Charger, ChargerConnector, ChargingSession,
    DriverDutyBreak, DriverDutyEvent, DriverAssetMismatch, DriverProfile,
    DriverSchedule, DutySession, HALCommand, Principal, ServiceJob,
    ServiceVehicle,
)
from cow.services import driver_operational as clearance
from cow.services import operational_eligibility as safety
from cow.services import rbac

MAX_SCHEDULE = timedelta(hours=24)
TERMINAL_JOBS = frozenset({"COMPLETED", "CANCELLED"})


def _reason(raw: str) -> str:
    clean = raw.strip() if isinstance(raw, str) else ""
    if not (8 <= len(clean) <= 500) or any(
        unicodedata.category(ch).startswith("C") for ch in clean
    ):
        reject(422, "INVALID_REASON", "Printable reason must be 8 to 500 characters")
    return clean


def _aware(dt) -> bool:
    return dt is not None and timezone.is_aware(dt)


def _window(start, end, *, now=None):
    if not _aware(start) or not _aware(end) or not start < end:
        reject(422, "INVALID_SCHEDULE_WINDOW", "Offset-aware start and end are required")
    if end - start > MAX_SCHEDULE:
        reject(422, "INVALID_SCHEDULE_WINDOW", "Schedule occurrence may not exceed 24 hours")
    if now is not None and end <= now:
        reject(422, "SCHEDULE_IN_PAST", "A new schedule must end in the future")


def _driver(principal_id):
    """Call after acquiring the operational gate; never trust a cached token snapshot."""
    principal = Principal.objects.select_for_update(of=("self",)).select_related(
        "account"
    ).filter(pk=principal_id, plane="driver").first()
    if principal is None:
        reject(404, "DRIVER_NOT_FOUND", "Driver not found")
    profile = DriverProfile.objects.select_for_update().filter(
        principal=principal
    ).first()
    if profile is None:
        reject(409, "DRIVER_PROFILE_MISSING", "Driver profile missing")
    return principal, profile


def _self(actor):
    if actor.plane != "driver":
        reject(403, "PRINCIPAL_PLANE_MISMATCH", "Driver identity required")
    return _driver(actor.pk)


def _clear(profile):
    # C2 derives fresh C1 review evidence and dates. Stored ACTIVE is insufficient.
    issues = clearance.effective_issues(profile)
    if issues:
        reject(409, "DRIVER_NOT_ELIGIBLE", ", ".join(issues))


def _assets(vehicle_id, charger_id, now):
    # Locks MUST follow the global gate, principal/profile, and then vehicle -> charger.
    vehicle = ServiceVehicle.objects.select_for_update().filter(pk=vehicle_id).first()
    charger = Charger.objects.select_for_update().filter(pk=charger_id).first()
    if vehicle is None or charger is None:
        reject(409, "ASSET_NOT_OPERATIONAL", "Scheduled vehicle/charger missing")
    safety.assert_duty_assets(vehicle, charger, now)
    if not ChargerConnector.objects.filter(charger=charger, active=True).exists():
        reject(409, "CONNECTOR_NOT_OPERATIONAL", "No active charger connector")
    return vehicle, charger


def _conflicting_schedule(driver_id, vehicle_id, charger_id, start, end, exclude_id=None):
    overlapping = DriverSchedule.objects.filter(
        status="PUBLISHED", starts_at__lt=end, ends_at__gt=start,
    ).filter(
        Q(driver_id=driver_id) | Q(vehicle_id=vehicle_id) | Q(charger_id=charger_id)
    )
    if exclude_id is not None:
        overlapping = overlapping.exclude(pk=exclude_id)
    if overlapping.exists():
        reject(409, "SCHEDULE_OVERLAP", "Driver or reserved asset has overlapping schedule")


def _schedule_data(schedule):
    return {
        "id": str(schedule.pk),
        "driver_id": str(schedule.driver.principal_id),
        "vehicle_id": str(schedule.vehicle_id),
        "charger_id": str(schedule.charger_id),
        "starts_at": schedule.starts_at.isoformat(),
        "ends_at": schedule.ends_at.isoformat(),
        "status": schedule.status,
        "version": schedule.version,
        "created_at": schedule.created_at.isoformat(),
    }


def _schedule_for_update(schedule_id):
    schedule = DriverSchedule.objects.select_for_update(of=("self",)).select_related(
        "driver", "driver__principal", "vehicle", "charger"
    ).filter(pk=schedule_id).first()
    if schedule is None:
        reject(404, "SCHEDULE_NOT_FOUND", "Schedule occurrence not found")
    return schedule


def _admin_schedule(actor, capability="schedules.manage"):
    # Lock ordering: Admin RBAC global -> operational gate -> principal -> assets.
    actor = safety.fresh_admin(actor, capability)
    safety.lock_gate()
    return actor


def _audit(actor, target_type, target_id, action, **details):
    AuditEvent.objects.create(
        actor=actor, target_type=target_type, target_id=target_id,
        action=action, details=details,
    )


def create_schedule(actor, data):
    start, end = data["starts_at"], data["ends_at"]
    _window(start, end, now=timezone.now())
    with transaction.atomic():
        actor = _admin_schedule(actor)
        _, profile = _driver(data["driver_id"])
        _clear(profile)
        # Acquire vehicle/charger locks in canonical order before overlap check.
        _assets(data["vehicle_id"], data["charger_id"], timezone.now())
        _conflicting_schedule(profile.pk, data["vehicle_id"], data["charger_id"], start, end)
        schedule = DriverSchedule.objects.create(
            driver=profile, vehicle_id=data["vehicle_id"], charger_id=data["charger_id"],
            starts_at=start, ends_at=end, status="PUBLISHED", created_by=actor,
            notes=data.get("notes", ""),
        )
        _audit(actor, "DriverSchedule", schedule.pk, "SCHEDULE_CREATED", version=1)
        return _schedule_data(schedule)


def amend_schedule(actor, schedule_id, expected_version, data):
    reason = _reason(data["reason"])
    with transaction.atomic():
        actor = _admin_schedule(actor)
        schedule = _schedule_for_update(schedule_id)
        if schedule.version != expected_version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Schedule changed; reload")
        if schedule.status != "PUBLISHED" or DutySession.objects.filter(schedule=schedule).exists():
            reject(409, "SCHEDULE_IMMUTABLE", "Started or cancelled schedule is immutable")
        _, profile = _driver(schedule.driver.principal_id)
        _clear(profile)
        start = data.get("starts_at") or schedule.starts_at
        end = data.get("ends_at") or schedule.ends_at
        vehicle_id = data.get("vehicle_id") or schedule.vehicle_id
        charger_id = data.get("charger_id") or schedule.charger_id
        _window(start, end, now=timezone.now())
        _assets(vehicle_id, charger_id, timezone.now())
        _conflicting_schedule(profile.pk, vehicle_id, charger_id, start, end, schedule.pk)
        if (start, end, vehicle_id, charger_id) == (
            schedule.starts_at, schedule.ends_at, schedule.vehicle_id, schedule.charger_id
        ):
            reject(422, "SCHEDULE_UNCHANGED", "At least one schedule field must change")
        schedule.starts_at, schedule.ends_at = start, end
        schedule.vehicle_id, schedule.charger_id = vehicle_id, charger_id
        schedule.version += 1
        schedule.save(update_fields=["starts_at", "ends_at", "vehicle", "charger", "version", "updated_at"])
        _audit(actor, "DriverSchedule", schedule.pk, "SCHEDULE_AMENDED",
               reason=reason, version=schedule.version)
        return _schedule_data(schedule)


def cancel_schedule(actor, schedule_id, expected_version, reason):
    reason = _reason(reason)
    with transaction.atomic():
        actor = _admin_schedule(actor)
        schedule = _schedule_for_update(schedule_id)
        if schedule.version != expected_version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Schedule changed; reload")
        if schedule.status != "PUBLISHED" or DutySession.objects.filter(schedule=schedule).exists():
            reject(409, "SCHEDULE_IMMUTABLE", "Started or cancelled schedule cannot be cancelled")
        schedule.status = "CANCELLED"
        schedule.version += 1
        schedule.save(update_fields=["status", "version", "updated_at"])
        _audit(actor, "DriverSchedule", schedule.pk, "SCHEDULE_CANCELLED",
               reason=reason, version=schedule.version)
        return _schedule_data(schedule)


def list_admin_schedules(actor, *, limit=50):
    rbac.require(actor, "schedules.read")
    if not 1 <= limit <= 100:
        reject(422, "INVALID_PAGE_LIMIT", "Limit must be 1 to 100")
    rows = DriverSchedule.objects.select_related("driver", "driver__principal").order_by(
        "-starts_at", "-pk"
    )[:limit]
    return {"items": [_schedule_data(row) for row in rows]}


def get_admin_schedule(actor, schedule_id):
    rbac.require(actor, "schedules.read")
    schedule = DriverSchedule.objects.select_related("driver", "driver__principal").filter(
        pk=schedule_id
    ).first()
    if schedule is None:
        reject(404, "SCHEDULE_NOT_FOUND", "Schedule occurrence not found")
    return _schedule_data(schedule)


def list_own_schedules(actor, *, limit=50):
    if actor.plane != "driver":
        reject(403, "PRINCIPAL_PLANE_MISMATCH", "Driver identity required")
    if not 1 <= limit <= 100:
        reject(422, "INVALID_PAGE_LIMIT", "Limit must be 1 to 100")
    rows = DriverSchedule.objects.select_related("driver", "driver__principal").filter(
        driver__principal_id=actor.pk
    ).order_by("-starts_at", "-pk")[:limit]
    return {"items": [_schedule_data(row) for row in rows]}


def get_own_schedule(actor, schedule_id):
    if actor.plane != "driver":
        reject(403, "PRINCIPAL_PLANE_MISMATCH", "Driver identity required")
    row = DriverSchedule.objects.select_related("driver", "driver__principal").filter(
        pk=schedule_id, driver__principal_id=actor.pk
    ).first()
    if row is None:
        reject(404, "SCHEDULE_NOT_FOUND", "Schedule occurrence not found")
    return _schedule_data(row)


def _active_duty(profile):
    return DutySession.objects.select_for_update().filter(
        driver=profile, ended_at__isnull=True
    ).first()


def _public_duty(duty):
    unresolved = DriverAssetMismatch.objects.filter(
        duty=duty, status="UNRESOLVED"
    ).exists()
    reasons = []
    if duty.state != "AVAILABLE":
        reasons.append("DUTY_NOT_AVAILABLE")
    if unresolved:
        reasons.append("ASSET_MISMATCH_UNRESOLVED")
    # Location freshness and dispatch offer service are outside C3. The
    # projection MUST NOT claim dispatchability without that authority.
    reasons.extend(["LOCATION_NOT_VERIFIED", "DISPATCH_NOT_ENABLED"])
    return {
        "id": str(duty.pk), "driver_id": str(duty.driver.principal_id),
        "vehicle_id": str(duty.vehicle_id), "charger_id": str(duty.charger_id),
        "schedule_id": str(duty.schedule_id) if duty.schedule_id else None,
        "state": duty.state, "version": duty.version,
        "started_at": duty.started_at.isoformat(),
        "ended_at": duty.ended_at.isoformat() if duty.ended_at else None,
        "dispatchable": False, "unavailable_reasons": reasons,
    }


def _event(duty, actor, action, before, after, reason):
    DriverDutyEvent.objects.create(
        duty=duty, actor=actor, action=action, from_state=before,
        to_state=after, result_version=duty.version, reason=reason,
    )
    _audit(actor, "DutySession", duty.pk, "DUTY_" + action,
           from_state=before, to_state=after, version=duty.version,
           reason=reason)


def _require_current(profile, expected_version):
    duty = _active_duty(profile)
    if duty is None:
        reject(409, "NO_ACTIVE_DUTY", "No active duty session")
    if duty.version != expected_version:
        reject(412, "RESOURCE_VERSION_MISMATCH", "Duty changed; reload")
    return duty


def _live_work(duty):
    blockers = []
    jobs = ServiceJob.objects.filter(driver_duty=duty)
    if jobs.exclude(state__in=TERMINAL_JOBS).exists():
        blockers.append("UNRESOLVED_DUTY_JOB")
    if ChargingSession.objects.filter(job__driver_duty=duty).exclude(state="ENDED").exists():
        blockers.append("UNRESOLVED_DUTY_CHARGING")
    if HALCommand.objects.filter(job__driver_duty=duty,
                                 state__in=safety.BUSY_COMMAND_STATES).exists():
        blockers.append("UNRESOLVED_DUTY_HAL")
    return blockers


def _check_no_live_work(duty):
    blockers = _live_work(duty)
    if blockers:
        reject(409, "DUTY_WORK_UNRESOLVED", ", ".join(blockers))


def start_duty(actor, schedule_id):
    with transaction.atomic():
        safety.lock_gate()
        principal, profile = _self(actor)
        _clear(profile)
        if _active_duty(profile) is not None:
            reject(409, "DUTY_ALREADY_ACTIVE", "Driver already has an open duty")
        schedule = _schedule_for_update(schedule_id)
        now = timezone.now()
        if schedule.driver_id != profile.pk:
            reject(404, "SCHEDULE_NOT_FOUND", "Schedule occurrence not found")
        if schedule.status != "PUBLISHED" or not schedule.starts_at <= now < schedule.ends_at:
            reject(409, "SCHEDULE_NOT_CURRENT", "No current published schedule occurrence")
        _assets(schedule.vehicle_id, schedule.charger_id, now)
        # DB partial unique claims are the final race defense.
        if DutySession.objects.filter(ended_at__isnull=True).filter(
            Q(vehicle_id=schedule.vehicle_id) | Q(charger_id=schedule.charger_id)
        ).exists():
            reject(409, "ASSET_ALREADY_CLAIMED", "Scheduled asset already in duty")
        if DutySession.objects.filter(schedule=schedule).exists():
            reject(409, "SCHEDULE_ALREADY_USED", "Schedule occurrence was already started")
        duty = DutySession.objects.create(
            driver=profile, schedule=schedule, vehicle_id=schedule.vehicle_id,
            charger_id=schedule.charger_id, state="AVAILABLE",
            started_at=now, version=1,
            observed_pairing={
                "scheduled_vehicle_id": str(schedule.vehicle_id),
                "scheduled_charger_id": str(schedule.charger_id),
            },
        )
        _event(duty, principal, "START", "OFF_DUTY", "AVAILABLE", "Scheduled duty commenced")
        return _public_duty(duty)


def current_duty(actor):
    if actor.plane != "driver":
        reject(403, "PRINCIPAL_PLANE_MISMATCH", "Driver identity required")
    duty = DutySession.objects.select_related("driver", "driver__principal").filter(
        driver__principal_id=actor.pk, ended_at__isnull=True
    ).first()
    return _public_duty(duty) if duty else None


def eligible_assets(actor):
    duty = current_duty(actor)
    if duty is None:
        return {"eligible": False, "reason": "NO_ACTIVE_DUTY", "items": []}
    return {"eligible": duty["state"] == "AVAILABLE", "reason": (
        "ADVISORY_ONLY_NOT_DISPATCH_AUTHORITY"
    ), "items": [{"vehicle_id": duty["vehicle_id"], "charger_id": duty["charger_id"]}]}


def start_break(actor, expected_version, reason):
    reason = _reason(reason)
    with transaction.atomic():
        safety.lock_gate()
        principal, profile = _self(actor)
        duty = _require_current(profile, expected_version)
        if duty.state != "AVAILABLE":
            reject(409, "DUTY_STATE_CONFLICT", "Break requires AVAILABLE duty")
        _check_no_live_work(duty)
        if DriverDutyBreak.objects.filter(duty=duty, ended_at__isnull=True).exists():
            reject(409, "BREAK_ALREADY_ACTIVE", "Break already active")
        now = timezone.now()
        brk = DriverDutyBreak.objects.create(duty=duty, started_at=now, reason=reason)
        duty.state = "ON_BREAK"
        duty.version += 1
        duty.save(update_fields=["state", "version", "updated_at"])
        _event(duty, principal, "BREAK_START", "AVAILABLE", "ON_BREAK", reason)
        return {"break_id": str(brk.pk), "duty": _public_duty(duty)}


def end_break(actor, break_id, expected_version):
    with transaction.atomic():
        safety.lock_gate()
        principal, profile = _self(actor)
        duty = _require_current(profile, expected_version)
        if duty.state != "ON_BREAK":
            reject(409, "DUTY_STATE_CONFLICT", "Duty is not on break")
        brk = DriverDutyBreak.objects.select_for_update().filter(
            pk=break_id, duty=duty, ended_at__isnull=True
        ).first()
        if brk is None:
            reject(404, "BREAK_NOT_FOUND", "No active matching break")
        _clear(profile)
        _assets(duty.vehicle_id, duty.charger_id, timezone.now())
        if DriverAssetMismatch.objects.filter(duty=duty, status="UNRESOLVED").exists():
            reject(409, "ASSET_MISMATCH_UNRESOLVED", "Reported mismatch needs reconciliation")
        brk.ended_at = timezone.now()
        brk.save(update_fields=["ended_at"])
        duty.state = "AVAILABLE"
        duty.version += 1
        duty.save(update_fields=["state", "version", "updated_at"])
        _event(duty, principal, "BREAK_END", "ON_BREAK", "AVAILABLE", "Driver resumed duty")
        return _public_duty(duty)


def end_duty(actor, expected_version, reason):
    reason = _reason(reason)
    with transaction.atomic():
        safety.lock_gate()
        principal, profile = _self(actor)
        duty = _require_current(profile, expected_version)
        _check_no_live_work(duty)
        now = timezone.now()
        if duty.state == "ON_BREAK":
            DriverDutyBreak.objects.filter(duty=duty, ended_at__isnull=True).update(
                ended_at=now
            )
        before = duty.state
        duty.state = "OFF_DUTY"
        duty.ended_at = now
        duty.version += 1
        duty.save(update_fields=["state", "ended_at", "version", "updated_at"])
        _event(duty, principal, "END", before, "OFF_DUTY", reason)
        return _public_duty(duty)


def report_asset_mismatch(actor, expected_version, observed_vehicle_id, observed_charger_id, reason):
    reason = _reason(reason)
    with transaction.atomic():
        safety.lock_gate()
        principal, profile = _self(actor)
        duty = _require_current(profile, expected_version)
        if (observed_vehicle_id, observed_charger_id) == (duty.vehicle_id, duty.charger_id):
            reject(422, "NO_ASSET_MISMATCH", "Reported assets match scheduled assets")
        if DriverAssetMismatch.objects.filter(duty=duty, status="UNRESOLVED").exists():
            reject(409, "ASSET_MISMATCH_UNRESOLVED", "Existing mismatch needs review")
        mismatch = DriverAssetMismatch.objects.create(
            duty=duty, reported_by=principal, expected_vehicle_id=duty.vehicle_id,
            expected_charger_id=duty.charger_id,
            observed_vehicle_id=observed_vehicle_id,
            observed_charger_id=observed_charger_id, reason=reason,
        )
        previous = duty.state
        if duty.state == "AVAILABLE":
            duty.state = "HELD"
        # During a live job, retain the actual state; block NEW physical starts
        # in operational_eligibility using unresolved mismatch evidence.
        duty.version += 1
        duty.save(update_fields=["state", "version", "updated_at"])
        _event(duty, principal, "MISMATCH_REPORT", previous, duty.state, reason)
        return {"mismatch_id": str(mismatch.pk), "duty": _public_duty(duty)}


def _admin_duty(actor, duty_id):
    rbac.require(actor, "schedules.read")
    duty = DutySession.objects.select_related("driver", "driver__principal").filter(
        pk=duty_id
    ).first()
    if duty is None:
        reject(404, "DUTY_NOT_FOUND", "Duty not found")
    return _public_duty(duty)


def admin_duty(actor, duty_id):
    return _admin_duty(actor, duty_id)


def admin_duties(actor, *, limit=50):
    rbac.require(actor, "schedules.read")
    if not 1 <= limit <= 100:
        reject(422, "INVALID_PAGE_LIMIT", "Limit must be 1 to 100")
    rows = DutySession.objects.select_related("driver", "driver__principal").order_by(
        "-started_at", "-pk"
    )[:limit]
    return {"items": [_public_duty(duty) for duty in rows]}


def admin_hold(actor, duty_id, expected_version, reason, release=False):
    reason = _reason(reason)
    with transaction.atomic():
        # Until an explicit drivers.operate capability is approved, require
        # BOTH existing admin permissions. Explicit DENY still prevails.
        actor = _admin_schedule(actor, capability="schedules.manage")
        rbac.require(actor, "drivers.manage")
        driver_id = DutySession.objects.filter(pk=duty_id, ended_at__isnull=True).values_list(
            "driver__principal_id", flat=True
        ).first()
        if driver_id is None:
            reject(404, "DUTY_NOT_FOUND", "Active duty not found")
        _, profile = _driver(driver_id)
        duty = DutySession.objects.select_for_update().filter(pk=duty_id, ended_at__isnull=True).first()
        if duty is None or duty.driver_id != profile.pk:
            reject(404, "DUTY_NOT_FOUND", "Active duty not found")
        if duty.version != expected_version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Duty changed; reload")
        expected_state, target_state = ("HELD", "AVAILABLE") if release else ("AVAILABLE", "HELD")
        if duty.state != expected_state:
            reject(409, "DUTY_STATE_CONFLICT", "Invalid duty hold transition")
        _check_no_live_work(duty)
        if release:
            _clear(profile)
            if duty.schedule_id:
                schedule = _schedule_for_update(duty.schedule_id)
                now = timezone.now()
                if schedule.status != "PUBLISHED" or not schedule.starts_at <= now < schedule.ends_at:
                    reject(409, "SCHEDULE_NOT_CURRENT", "Schedule is no longer current")
            _assets(duty.vehicle_id, duty.charger_id, timezone.now())
            if DriverAssetMismatch.objects.filter(duty=duty, status="UNRESOLVED").exists():
                reject(409, "ASSET_MISMATCH_UNRESOLVED", "Mismatch review still pending")
        duty.state = target_state
        duty.version += 1
        duty.save(update_fields=["state", "version", "updated_at"])
        _event(duty, actor, "RELEASE_HOLD" if release else "HOLD", expected_state,
               target_state, reason)
        return _public_duty(duty)
