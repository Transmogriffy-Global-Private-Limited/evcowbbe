"""C2 Driver clearance and operational lifecycle.

C1 onboarding is historical recruitment evidence, not operational permission.
All consequential writers participate in Stage B's shared PostgreSQL gate.
The workflow intentionally has no implied emergency physical closeout.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from cow.common.errors import reject
from cow.models import (
    AuditEvent, ChargingSession, DriverApplication, DriverDocumentReview,
    DriverOperationalDecision, DriverProfile, DutySession, HALCommand,
    Principal, ServiceJob,
)
from cow.services import driver_recruitment as recruitment
from cow.services import operational_eligibility as safety
from cow.services import rbac

LOCAL_TZ = ZoneInfo("Asia/Kolkata")
STATES = frozenset({"PENDING", "ACTIVE", "SUSPENDED", "OFFBOARDED"})
ACTIONS = {
    "clear": (frozenset({"PENDING"}), "ACTIVE", "drivers.manage"),
    "suspend": (frozenset({"ACTIVE"}), "SUSPENDED", "drivers.suspend"),
    "reinstate": (frozenset({"SUSPENDED"}), "ACTIVE", "drivers.suspend"),
    "offboard": (frozenset({"PENDING", "ACTIVE", "SUSPENDED"}), "OFFBOARDED", "drivers.suspend"),
}
LIVE_JOB_STATES = frozenset({
    "DISPATCHING", "ASSIGNED", "ACCEPTED", "EN_ROUTE", "ARRIVED",
    "SERVICE_PREPARING", "CHARGING", "WRAP_UP", "REASSIGNING",
})


def business_today():
    return timezone.localdate(timezone=LOCAL_TZ)


def _reason(value):
    clean = value.strip() if isinstance(value, str) else ""
    if not 8 <= len(clean) <= 500 or any(
        unicodedata.category(char).startswith("C") for char in clean
    ):
        reject(422, "INVALID_REASON", "A reason of 8 to 500 printable characters is required")
    return clean


def _application(principal_id):
    return DriverApplication.objects.select_related("policy").filter(principal_id=principal_id).first()


def evidence_issues(profile, *, app=None, today=None):
    """Fail-closed derived evidence, never the current Principal/eligibility check.

    Must be called while holding the operational gate for authoritative writers.
    Read-only projections may call it without the gate and are labelled advisory.
    """
    today = today or business_today()
    app = app if app is not None else _application(profile.principal_id)
    issues = []
    if app is None or app.state != "ACTIVE":
        issues.append("RECRUITMENT_NOT_ACTIVE")
        return issues
    if app.policy_id is None:
        issues.append("RECRUITMENT_POLICY_MISSING")
        return issues
    required = set(app.policy.required_document_types)
    required.add("DRIVING_LICENCE")  # Operational driving needs an actual verified licence.
    latest = recruitment._documents(app)
    for kind in sorted(required):
        doc = latest.get(kind)
        if doc is None:
            issues.append("REQUIRED_DOCUMENT_MISSING")
            continue
        if kind == "DRIVING_LICENCE" and doc.expires_on is None:
            issues.append("DRIVING_LICENCE_EXPIRY_MISSING")
        if doc.expires_on is not None and doc.expires_on < today:
            issues.append("REQUIRED_DOCUMENT_EXPIRED")
        if doc.review_status != "ACCEPTED":
            issues.append("REQUIRED_DOCUMENT_NOT_ACCEPTED")
        # Review status by itself is not immutable approval provenance.
        if not DriverDocumentReview.objects.filter(document_id=doc.pk, decision="ACCEPTED").exists():
            issues.append("DOCUMENT_REVIEW_EVIDENCE_MISSING")
        if app.policy.scan_required and doc.scan_status != "CLEAN":
            issues.append("REQUIRED_DOCUMENT_NOT_SCANNED")
    licence = latest.get("DRIVING_LICENCE")
    if profile.licence_valid_until is None or profile.licence_valid_until < today:
        issues.append("DRIVING_LICENCE_INVALID")
    if licence is not None and profile.licence_valid_until != licence.expires_on:
        issues.append("DRIVING_LICENCE_PROFILE_MISMATCH")
    return sorted(set(issues))


def effective_issues(profile, *, app=None, today=None):
    """Full current operational clearance, including expiry and C1 evidence.

    The stored ACTIVE status alone never creates runtime dispatch/charge authority.
    """
    issues = []
    principal = profile.principal
    if principal.plane != "driver" or principal.status != "ACTIVE" or not principal.account.email_verified:
        issues.append("DRIVER_ACCOUNT_NOT_READY")
    if profile.eligibility != "ACTIVE":
        issues.append("OPERATIONAL_" + str(profile.eligibility))
    else:
        # Migration 0012 does not fabricate clearance for pre-existing ACTIVE
        # profiles. A matching immutable positive decision must exist.
        if not DriverOperationalDecision.objects.filter(
            driver_id=profile.pk, result_version=profile.version,
            to_status="ACTIVE", action__in=("CLEAR", "REINSTATE"),
        ).exists():
            issues.append("OPERATIONAL_CLEARANCE_UNRECORDED")
    issues.extend(evidence_issues(profile, app=app, today=today))
    return sorted(set(issues))


def _manifest(app):
    """Identity-only evidence manifest: never persist document bytes, keys or PII."""
    latest = recruitment._documents(app)
    items = [{
        "type": kind, "id": str(doc.pk), "review": doc.review_status,
        "scan": doc.scan_status, "valid_until": doc.expires_on.isoformat() if doc.expires_on else None,
    } for kind, doc in sorted(latest.items())]
    digest = hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest


def _quiescence_blockers(profile):
    """Normal administrative changes cannot strand real operational work."""
    blockers = []
    jobs = ServiceJob.objects.filter(driver_duty__driver=profile)
    if DutySession.objects.filter(driver=profile, ended_at__isnull=True).exists():
        blockers.append("OPEN_DRIVER_DUTY")
    if jobs.filter(state__in=LIVE_JOB_STATES).exists():
        blockers.append("ACTIVE_DRIVER_JOB")
    if ChargingSession.objects.filter(job__driver_duty__driver=profile, state="ACTIVE").exists():
        blockers.append("ACTIVE_DRIVER_CHARGING")
    if HALCommand.objects.filter(
        job__driver_duty__driver=profile, state__in=safety.BUSY_COMMAND_STATES
    ).exists():
        blockers.append("UNRESOLVED_DRIVER_HAL_COMMAND")
    return blockers


def _encode(profile, *, advisory=False):
    issues = effective_issues(profile)
    app = _application(profile.principal_id)
    return {
        "driver_id": str(profile.principal_id),
        "profile_id": str(profile.pk),
        "eligibility": profile.eligibility,
        "version": profile.version,
        "effective_eligible": not issues,
        "reasons": issues,
        "licence_valid_until": profile.licence_valid_until.isoformat() if profile.licence_valid_until else None,
        "recruitment_state": app.state if app else None,
        "recruitment_policy_version": app.policy.version if app and app.policy_id else None,
        "advisory_read": advisory,
    }


def read_admin(actor, driver_id):
    rbac.require(actor, "drivers.read")
    profile = DriverProfile.objects.select_related("principal", "principal__account").filter(
        principal_id=driver_id, principal__plane="driver"
    ).first()
    if profile is None:
        reject(404, "DRIVER_NOT_FOUND", "Driver profile not found")
    return _encode(profile, advisory=True)


def read_self(actor):
    if actor.plane != "driver":
        reject(403, "PRINCIPAL_PLANE_MISMATCH", "Driver identity required")
    profile = DriverProfile.objects.select_related("principal", "principal__account").filter(
        principal_id=actor.pk
    ).first()
    if profile is None:
        return {"driver_id": str(actor.pk), "eligibility": "PENDING",
                "version": None, "effective_eligible": False,
                "reasons": ["OPERATIONAL_PROFILE_MISSING"], "advisory_read": True,
                "profile_id": None, "licence_valid_until": None,
                "recruitment_state": None, "recruitment_policy_version": None}
    return _encode(profile, advisory=True)


def decide(actor, driver_id, expected_version, action, reason):
    """One atomic eligibility decision and immutable provenance.

    Normal suspension/offboarding refuse active work. Emergency hold/closeout
    needs a separate incident + HAL recovery contract and is not emulated here.
    """
    if action not in ACTIONS:
        reject(404, "OPERATIONAL_ACTION_UNKNOWN", "Unknown operational action")
    sources, target, capability = ACTIONS[action]
    reason = _reason(reason)
    with transaction.atomic():
        actor = safety.fresh_admin(actor, capability)  # RBAC global lock first
        if action in {"reinstate", "offboard"}:
            # A positive reinstatement or permanent offboarding needs both
            # workforce management and suspension authority.
            rbac.require(actor, "drivers.manage")
        safety.lock_gate()
        driver = Principal.objects.select_for_update(of=("self",)).select_related("account").filter(
            pk=driver_id, plane="driver"
        ).first()
        if driver is None:
            reject(404, "DRIVER_NOT_FOUND", "Driver not found")
        profile = DriverProfile.objects.select_for_update().filter(principal=driver).first()
        if profile is None:
            reject(409, "DRIVER_PROFILE_MISSING", "Driver profile does not exist")
        if profile.version != expected_version:
            reject(412, "RESOURCE_VERSION_MISMATCH", "Driver changed; reload current version")
        if profile.eligibility not in sources:
            reject(409, "DRIVER_OPERATIONAL_STATE_CONFLICT", "Invalid operational transition")
        app = _application(driver.pk)
        # Even a positive clearance cannot ratify an inconsistent live duty
        # created by a bypassing writer. No transition closes physical work.
        blockers = _quiescence_blockers(profile)
        if blockers:
            reject(409, "DRIVER_OPERATION_BLOCKED", ", ".join(blockers))
        if target == "ACTIVE":
            # Every positive grant rechecks current verified C1 evidence, not a cached C1 approval.
            if driver.status != "ACTIVE" or not driver.account.email_verified:
                reject(409, "DRIVER_ACCOUNT_NOT_READY", "Driver identity is not active and verified")
            issues = evidence_issues(profile, app=app)
            if issues:
                reject(409, "DRIVER_CLEARANCE_BLOCKED", ", ".join(issues))
        old = profile.eligibility
        profile.eligibility = target
        profile.version += 1
        profile.save(update_fields=["eligibility", "version", "updated_at"])
        decision = DriverOperationalDecision.objects.create(
            driver=profile, actor=actor, action=action.upper(),
            from_status=old, to_status=target, reason=reason,
            result_version=profile.version, application=app, application_version=app.version if app else None,
            policy=app.policy if app and app.policy_id else None,
            licence_valid_until=profile.licence_valid_until,
            evidence_digest=_manifest(app) if app else None,
        )
        AuditEvent.objects.create(
            actor=actor, target_type="DriverProfile", target_id=profile.pk,
            action="OPERATIONAL_" + action.upper(),
            details={"reason": reason, "from": old, "to": target,
                     "decision_id": str(decision.pk), "version": profile.version},
        )
        return _encode(profile)


def read_decisions(actor, driver_id, *, limit=50, before_at=None, before_id=None):
    """Bounded private Admin history. The timestamp and UUID cursor travel together."""
    rbac.require(actor, "drivers.manage")
    if not 1 <= limit <= 100:
        reject(422, "INVALID_PAGE_LIMIT", "Page limit must be 1..100")
    if (before_at is None) != (before_id is None):
        reject(422, "INVALID_CURSOR", "Both before_at and before_id are required")
    if before_at is not None and (before_at.tzinfo is None or before_at.utcoffset() is None):
        reject(422, "INVALID_CURSOR", "Cursor timestamp needs an offset")
    profile = DriverProfile.objects.filter(
        principal_id=driver_id, principal__plane="driver"
    ).first()
    if profile is None:
        reject(404, "DRIVER_NOT_FOUND", "Driver profile not found")
    qs = DriverOperationalDecision.objects.filter(driver=profile)
    if before_at is not None:
        qs = qs.filter(Q(occurred_at__lt=before_at) |
                       Q(occurred_at=before_at, id__lt=before_id))
    rows = list(qs.order_by("-occurred_at", "-id")[:limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [{
        "id": str(row.pk), "action": row.action,
        "from_status": row.from_status, "to_status": row.to_status,
        "reason": row.reason, "actor_id": str(row.actor_id),
        "version": row.result_version,
        "occurred_at": row.occurred_at.isoformat(),
    } for row in rows],
        "next_before_at": rows[-1].occurred_at.isoformat() if has_more and rows else None,
        "next_before_id": str(rows[-1].pk) if has_more and rows else None,
    }
