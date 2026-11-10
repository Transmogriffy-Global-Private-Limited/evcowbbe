"""C1 recruitment domain. Application approval is NOT dispatch eligibility.

All Admin writes reuse Stage B's RBAC -> global operational gate lock order.
The version is a logical compare-and-swap, not a substitute for idempotency.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from cow.common.errors import reject
from cow.models import (
    AuditEvent, DriverApplication, DriverApplicationHistory, DriverDocument,
    DriverDocumentReview, DriverProfile, DriverRecruitmentPolicy, Principal,
)
from cow.services import operational_eligibility as safety, rbac
from cow.services.private_documents import StoredFile, store

DOCUMENT_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

def _today():
    return timezone.localdate(timezone=ZoneInfo("Asia/Kolkata"))


def _reason(value: str) -> str:
    clean = value.strip() if isinstance(value, str) else ""
    if not 8 <= len(clean) <= 500 or any(unicodedata.category(c).startswith("C") for c in clean):
        reject(422, "INVALID_REASON", "A meaningful reason of 8 to 500 characters is required")
    return clean


def _driver(actor):
    if actor.plane != "driver" or actor.status not in {"PENDING", "ACTIVE"} or not actor.account.email_verified:
        reject(403, "DRIVER_ACCOUNT_NOT_READY", "Verified Driver credentials required")


def _policy():
    return DriverRecruitmentPolicy.objects.filter(policy_key="DEFAULT", active=True).first()


def policy_view(obj, *, admin=False):
    if obj is None:
        return {"configured": False, "required_document_types": [], "scan_required": True, "version": None}
    value = {"configured": True, "required_document_types": obj.required_document_types,
             "scan_required": obj.scan_required, "version": obj.version}
    if admin:
        value.update({"id": str(obj.pk), "active": obj.active, "legal_decision_ref": obj.legal_decision_ref})
    return value


def application_view(app):
    if app is None:
        return None
    return {"id": str(app.pk), "driver_id": str(app.principal_id), "state": app.state,
            "version": app.version, "legal_name": app.legal_name, "contact_phone": app.contact_phone,
            "policy_version": app.policy.version if app.policy_id else None,
            "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
            "created_at": app.created_at.isoformat()}


def document_view(doc):
    return {"id": str(doc.pk), "document_type": doc.document_type, "original_filename": doc.original_filename,
            "content_type": doc.content_type, "size_bytes": doc.size_bytes, "sha256": doc.sha256,
            "expires_on": doc.expires_on.isoformat() if doc.expires_on else None,
            "replaces_id": str(doc.replaces_id) if doc.replaces_id else None,
            "review_status": doc.review_status, "scan_status": doc.scan_status,
            "created_at": doc.created_at.isoformat()}


def _documents(app):
    """Newest version by document type; older evidence remains addressable."""
    latest = {}
    for doc in DriverDocument.objects.filter(application=app).order_by("-created_at", "-id"):
        latest.setdefault(doc.document_type, doc)
    return latest


def _requirements(app, *, verified):
    policy = app.policy
    if policy is None:
        reject(409, "DRIVER_POLICY_REQUIRED", "A reviewed recruitment policy is not configured")
    latest = _documents(app)
    today = _today()
    missing = []
    for kind in policy.required_document_types:
        doc = latest.get(kind)
        if doc is None or (doc.expires_on and doc.expires_on < today) or (kind == "DRIVING_LICENCE" and doc.expires_on is None):
            missing.append(kind)
        elif verified and (doc.review_status != "ACCEPTED" or
                           (policy.scan_required and doc.scan_status != "CLEAN")):
            missing.append(kind)
    if missing:
        reject(409, "DRIVER_DOCUMENTS_INCOMPLETE", "Required current document evidence is missing or unverified")


def _history(app, actor, previous, reason):
    DriverApplicationHistory.objects.create(
        application=app, actor=actor, from_state=previous,
        to_state=app.state, reason=reason,
    )
    AuditEvent.objects.create(
        actor=actor, target_type="DriverApplication", target_id=app.pk,
        action=app.state, details={"from": previous, "reason": reason},
    )


def _version(app, expected):
    actual = app.version if app is not None else 0
    if actual != expected:
        reject(412, "RESOURCE_VERSION_MISMATCH", "Application changed; reload the latest version")


def read_own(actor):
    _driver(actor)
    app = DriverApplication.objects.select_related("policy").filter(principal_id=actor.pk).first()
    return {"application": application_view(app),
            "requirements": policy_view(_policy()),
            "documents": [document_view(d) for d in _documents(app).values()] if app else []}


def update_own(actor, expected_version: int, legal_name: str, contact_phone: str):
    _driver(actor)
    name = legal_name.strip()
    phone = contact_phone.strip()
    if not 2 <= len(name) <= 160 or any(unicodedata.category(c).startswith("C") for c in name):
        reject(422, "INVALID_APPLICANT_NAME", "Applicant name is invalid")
    if not re.fullmatch(r"\+?[0-9 ()-]{7,32}", phone):
        reject(422, "INVALID_CONTACT_PHONE", "Contact phone is invalid")
    with transaction.atomic():
        safety.lock_gate()
        app = DriverApplication.objects.select_for_update().filter(principal_id=actor.pk).first()
        _version(app, expected_version)
        if app and app.state not in {"DRAFT", "ACTION_REQUIRED"}:
            reject(409, "APPLICATION_STATE_CONFLICT", "Application cannot currently be edited")
        if app is None:
            app = DriverApplication.objects.create(principal=actor, legal_name=name, contact_phone=phone)
            _history(app, actor, "NONE", "Applicant created draft")
        elif (app.legal_name, app.contact_phone) != (name, phone):
            app.legal_name, app.contact_phone = name, phone
            app.version += 1
            app.save(update_fields=["legal_name", "contact_phone", "version", "updated_at"])
        return application_view(app)


def _locked_own(actor):
    _driver(actor)
    app = DriverApplication.objects.select_for_update(of=("self",)).select_related("policy").filter(principal_id=actor.pk).first()
    if app is None:
        reject(404, "APPLICATION_NOT_FOUND", "Create an application first")
    return app


def upload_own(actor, expected_version: int, document_type: str, original_name: str,
               expires_on: date | None, stored: StoredFile):
    """Storage object exists before DB mutation; caller reclaims it on replay/failure."""
    with transaction.atomic():
        safety.lock_gate()
        app = _locked_own(actor)
        _version(app, expected_version)
        if app.state not in {"DRAFT", "ACTION_REQUIRED"}:
            reject(409, "APPLICATION_STATE_CONFLICT", "Document upload is not allowed in this state")
        if not DOCUMENT_TYPE.fullmatch(document_type):
            reject(422, "DOCUMENT_TYPE_INVALID", "Invalid document type")
        if expires_on and expires_on < _today():
            reject(422, "DOCUMENT_EXPIRED", "Expired documents cannot be submitted")
        if DriverDocument.objects.filter(application=app).count() >= 100 or DriverDocument.objects.filter(
            uploaded_by=actor, created_at__gte=timezone.now() - timedelta(hours=1)
        ).count() >= 10:
            reject(429, "DOCUMENT_UPLOAD_RATE_LIMITED", "Document upload limit reached")
        previous = _documents(app).get(document_type)
        doc = DriverDocument.objects.create(
            application=app, document_type=document_type,
            storage_key=stored.key, content_type=stored.content_type, size_bytes=stored.size,
            original_filename=original_name, sha256=stored.sha256, expires_on=expires_on,
            replaces=previous, uploaded_by=actor,
        )
        app.version += 1
        app.save(update_fields=["version", "updated_at"])
        AuditEvent.objects.create(actor=actor, target_type="DriverDocument", target_id=doc.pk,
            action="UPLOAD", details={"application_id": str(app.pk), "type": document_type,
                                      "replaces_id": str(previous.pk) if previous else None})
        return {"document": document_view(doc), "application_version": app.version}


def submit_own(actor, expected_version: int):
    with transaction.atomic():
        safety.lock_gate()
        app = _locked_own(actor)
        _version(app, expected_version)
        if app.state not in {"DRAFT", "ACTION_REQUIRED"}:
            reject(409, "APPLICATION_STATE_CONFLICT", "Application cannot be submitted")
        if not app.legal_name or not app.contact_phone:
            reject(409, "APPLICATION_INCOMPLETE", "Required applicant details are missing")
        policy = _policy()
        if policy is None:
            reject(409, "DRIVER_POLICY_REQUIRED", "Admin recruitment policy is not active")
        app.policy = policy
        _requirements(app, verified=False)
        previous = app.state
        app.state = "SUBMITTED"
        app.submitted_at = timezone.now()
        app.version += 1
        app.save(update_fields=["policy", "state", "submitted_at", "version", "updated_at"])
        _history(app, actor, previous, "Applicant submitted application")
        return application_view(app)


def withdraw_own(actor, expected_version: int, reason: str):
    with transaction.atomic():
        safety.lock_gate()
        app = _locked_own(actor)
        _version(app, expected_version)
        if app.state not in {"DRAFT", "SUBMITTED", "ACTION_REQUIRED"}:
            reject(409, "APPLICATION_STATE_CONFLICT", "Application cannot be withdrawn")
        previous = app.state
        app.state = "WITHDRAWN"
        app.version += 1
        app.save(update_fields=["state", "version", "updated_at"])
        _history(app, actor, previous, _reason(reason))
        return application_view(app)


def own_history(actor):
    _driver(actor)
    app = DriverApplication.objects.filter(principal_id=actor.pk).first()
    if app is None:
        return []
    return [{"from_state": h.from_state, "to_state": h.to_state,
             "reason": h.reason if h.actor_id == actor.pk or h.to_state in {"ACTION_REQUIRED", "REJECTED"} else "Admin review recorded",
             "created_at": h.created_at.isoformat()}
            for h in app.history.order_by("-created_at", "-id")[:100]]


def _locked_application(application_id):
    app = DriverApplication.objects.select_for_update(of=("self",)).select_related("policy", "principal").filter(pk=application_id).first()
    if app is None:
        reject(404, "APPLICATION_NOT_FOUND", "Application not found")
    return app


def list_applications(actor, *, state=None, before=None, limit=50):
    rbac.require(actor, "drivers.review")
    query = DriverApplication.objects.select_related("policy").order_by("-id")
    if state:
        query = query.filter(state=state)
    if before:
        query = query.filter(pk__lt=before)
    rows = list(query[:limit + 1])
    return {"items": [application_view(x) for x in rows[:limit]],
            "next_cursor": str(rows[limit - 1].pk) if len(rows) > limit else None}


def get_application(actor, application_id):
    rbac.require(actor, "drivers.review")
    app = DriverApplication.objects.select_related("policy").filter(pk=application_id).first()
    if app is None:
        reject(404, "APPLICATION_NOT_FOUND", "Application not found")
    return {"application": application_view(app),
            "policy": policy_view(app.policy, admin=True) if app.policy else None,
            "documents": [document_view(x) for x in app.documents.order_by("-created_at", "-id")[:100]],
            "history": [{"from_state": x.from_state, "to_state": x.to_state,
                         "reason": x.reason, "created_at": x.created_at.isoformat()}
                        for x in app.history.order_by("-created_at", "-id")[:100]]}


def _admin_transition(actor, application_id, expected_version, target, reason):
    allowed = {
        "UNDER_REVIEW": {"SUBMITTED"},
        "ACTION_REQUIRED": {"UNDER_REVIEW"},
        "APPROVED": {"UNDER_REVIEW"},
        "REJECTED": {"UNDER_REVIEW"},
        "ONBOARDING": {"APPROVED"},
        "ACTIVE": {"ONBOARDING"},
    }
    capability = "drivers.review" if target in {"UNDER_REVIEW", "ACTION_REQUIRED", "APPROVED", "REJECTED"} else "drivers.manage"
    with transaction.atomic():
        actor = safety.fresh_admin(actor, capability)
        safety.lock_gate()
        app = _locked_application(application_id)
        _version(app, expected_version)
        if app.state not in allowed[target]:
            reject(409, "APPLICATION_STATE_CONFLICT", "Invalid recruitment state transition")
        if target in {"APPROVED", "ONBOARDING", "ACTIVE"}:
            _requirements(app, verified=True)
        if target == "ACTIVE" and app.principal.status == "SUSPENDED":
            reject(409, "DRIVER_SUSPENDED", "Operational suspension cannot be bypassed by onboarding")
        previous = app.state
        app.state = target
        app.version += 1
        app.reviewed_at = timezone.now()
        app.save(update_fields=["state", "version", "reviewed_at", "updated_at"])
        _history(app, actor, previous, _reason(reason))
        if target == "ACTIVE":
            # Do not set eligibility=ACTIVE: Stage C2 performs verified operational clearance.
            p, _ = DriverProfile.objects.get_or_create(principal=app.principal, defaults={"eligibility": "PENDING"})
            licence = _documents(app).get("DRIVING_LICENCE")
            if licence is not None and licence.review_status == "ACCEPTED" and licence.expires_on:
                p.licence_valid_until = licence.expires_on
                p.save(update_fields=["licence_valid_until", "updated_at"])
            if app.principal.status != "ACTIVE":
                app.principal.status = "ACTIVE"
                app.principal.save(update_fields=["status", "updated_at"])
        return application_view(app)


def admin_transition(actor, application_id, expected_version, target, reason):
    return _admin_transition(actor, application_id, expected_version, target, reason)


def review_document(actor, application_id, document_id, expected_version, decision, reason):
    with transaction.atomic():
        actor = safety.fresh_admin(actor, "drivers.review")
        safety.lock_gate()
        app = _locked_application(application_id)
        _version(app, expected_version)
        if app.state != "UNDER_REVIEW":
            reject(409, "APPLICATION_STATE_CONFLICT", "Application is not under review")
        doc = DriverDocument.objects.select_for_update().filter(application=app, pk=document_id).first()
        if doc is None:
            reject(404, "DOCUMENT_NOT_FOUND", "Document not found")
        if _documents(app).get(doc.document_type).pk != doc.pk:
            reject(409, "DOCUMENT_SUPERSEDED", "Review the newest document version")
        store().verify(StoredFile(doc.storage_key, doc.size_bytes, doc.sha256, doc.content_type))
        doc.review_status = decision
        doc.save(update_fields=["review_status", "updated_at"])
        DriverDocumentReview.objects.create(document=doc, actor=actor, decision=decision, reason=_reason(reason))
        app.version += 1
        app.save(update_fields=["version", "updated_at"])
        AuditEvent.objects.create(actor=actor, target_type="DriverDocument", target_id=doc.pk,
            action=decision, details={"reason": reason, "application_id": str(app.pk)})
        return {"document": document_view(doc), "application_version": app.version}


def create_policy(actor, required_types, scan_required, decision_ref, reason):
    kinds = list(required_types)
    if not 1 <= len(kinds) <= 12 or len(set(kinds)) != len(kinds) or not all(DOCUMENT_TYPE.fullmatch(x) for x in kinds):
        reject(422, "INVALID_RECRUITMENT_POLICY", "Required document types are invalid")
    if not 8 <= len(decision_ref.strip()) <= 200:
        reject(422, "INVALID_RECRUITMENT_POLICY", "A business/legal decision reference is required")
    with transaction.atomic():
        actor = safety.fresh_admin(actor, "drivers.manage")
        safety.lock_gate()
        version = (DriverRecruitmentPolicy.objects.filter(policy_key="DEFAULT").order_by("-version")
                   .values_list("version", flat=True).first() or 0) + 1
        obj = DriverRecruitmentPolicy.objects.create(
            policy_key="DEFAULT", version=version, required_document_types=kinds,
            scan_required=scan_required, legal_decision_ref=decision_ref.strip(), created_by=actor,
        )
        AuditEvent.objects.create(actor=actor, target_type="DriverRecruitmentPolicy", target_id=obj.pk,
            action="CREATE", details={"reason": _reason(reason), "version": version})
        return policy_view(obj, admin=True)


def activate_policy(actor, policy_id, reason):
    with transaction.atomic():
        actor = safety.fresh_admin(actor, "drivers.manage")
        safety.lock_gate()
        obj = DriverRecruitmentPolicy.objects.select_for_update().filter(pk=policy_id).first()
        if obj is None:
            reject(404, "POLICY_NOT_FOUND", "Recruitment policy not found")
        if obj.active:
            reject(409, "POLICY_ALREADY_ACTIVE", "Policy is already active")
        DriverRecruitmentPolicy.objects.filter(policy_key=obj.policy_key, active=True).update(active=False)
        obj.active = True
        obj.save(update_fields=["active", "updated_at"])
        AuditEvent.objects.create(actor=actor, target_type="DriverRecruitmentPolicy", target_id=obj.pk,
            action="ACTIVATE", details={"reason": _reason(reason), "version": obj.version})
        return policy_view(obj, admin=True)


def list_policies(actor):
    rbac.require(actor, "drivers.review")
    return [policy_view(x, admin=True) for x in DriverRecruitmentPolicy.objects.order_by("-version")[:100]]


def document_for_download(actor, document_id, *, application_id=None):
    if application_id is None:
        _driver(actor)
        query = DriverDocument.objects.filter(pk=document_id, application__principal_id=actor.pk)
    else:
        rbac.require(actor, "drivers.review")
        query = DriverDocument.objects.filter(pk=document_id, application_id=application_id)
    doc = query.first()
    if doc is None:
        reject(404, "DOCUMENT_NOT_FOUND", "Document not found")
    # Exact ID, private storage; the audit is persisted BEFORE returning a stream.
    stored = StoredFile(doc.storage_key, doc.size_bytes, doc.sha256, doc.content_type)
    stream = store().open(stored.key)
    try:
        AuditEvent.objects.create(actor=actor, target_type="DriverDocument", target_id=doc.pk,
            action="DOWNLOAD", details={"application_id": str(doc.application_id)})
    except Exception:
        stream.close()
        raise
    return stream, doc
