"""Versioned Admin-only tariff policy lifecycle; deliberately no quotation activation."""

import hashlib
import json

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from cow.errors import reject
from cow.models import AuditEvent, PricingPolicy
from cow.pricing_contract import Draft, overlap


def encode(policy):
    return {
        "id": str(policy.id),
        "policy_key": policy.policy_key,
        "policy_version": policy.policy_version,
        "scope": policy.scope,
        "currency": policy.currency,
        "rules": policy.rules,
        "starts_at": policy.starts_at.isoformat() if policy.starts_at else None,
        "ends_at": policy.ends_at.isoformat() if policy.ends_at else None,
        "status": policy.status,
        "state_version": policy.state_version,
        "created_at": policy.created_at.isoformat(),
    }


def _get(policy_id, lock=False):
    qs = PricingPolicy.objects
    if lock:
        qs = qs.select_for_update()
    obj = qs.filter(id=policy_id, policy_key__isnull=False).first()
    if obj is None:
        reject(404, "NOT_FOUND", "Tariff revision not found")
    return obj


def get(policy_id):
    return _get(policy_id)


def list_policies(status="", limit=50):
    qs = PricingPolicy.objects.filter(policy_key__isnull=False)
    if status:
        qs = qs.filter(status=status)
    return [encode(x) for x in qs.order_by("-created_at", "-id")[:limit]]


def history(policy_id):
    _get(policy_id)
    events = AuditEvent.objects.filter(target_type="PricingPolicy", target_id=policy_id).order_by(
        "-occurred_at", "-id"
    )[:100]
    return [
        {
            "id": str(x.id),
            "action": x.action,
            "occurred_at": x.occurred_at.isoformat(),
            "details": x.details,
        }
        for x in events
    ]


def _scope_lock(scope):
    # Stable cross-process lock serializes every publish for a given operational scope.
    canonical = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    hashed = hashlib.sha256(("cow-pricing-scope-v1:" + canonical).encode()).digest()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)", [int.from_bytes(hashed[:8], "big", signed=True)]
        )


def _audit(actor, policy, action, reason):
    AuditEvent.objects.create(
        actor=actor,
        target_type="PricingPolicy",
        target_id=policy.id,
        action=action,
        details={
            "reason": reason,
            "state_version": policy.state_version,
            "policy_key": policy.policy_key,
            "policy_version": policy.policy_version,
        },
    )


def create(actor, draft: Draft):
    try:
        with transaction.atomic():
            _scope_lock(draft.scope.model_dump())
            if PricingPolicy.objects.filter(
                policy_key=draft.policy_key, policy_version=draft.policy_version
            ).exists():
                reject(409, "PRICING_REVISION_EXISTS", "Policy revision already exists")
            p = PricingPolicy.objects.create(
                name=draft.policy_key,
                revision=draft.policy_version,
                policy_key=draft.policy_key,
                policy_version=draft.policy_version,
                scope=draft.scope.model_dump(),
                rules=[r.model_dump(exclude_none=True) for r in draft.rules],
                currency="INR",
                starts_at=draft.starts_at,
                ends_at=draft.ends_at,
                created_by=actor,
                status="DRAFT",
                state_version=1,
            )
            _audit(actor, p, "CREATE_DRAFT", draft.reason)
    except IntegrityError:
        reject(409, "PRICING_REVISION_EXISTS", "Policy revision conflicts with existing records")
    return p


def _assert_version(policy, version):
    if policy.state_version != version:
        reject(412, "RESOURCE_VERSION_MISMATCH", "Tariff state changed; reload ETag")


def publish(actor, policy_id, version, reason):
    # Resource lock first to avoid reversed lock order. All publish operations
    # acquire the identical scope lock before a policy row lock.
    with transaction.atomic():
        scope = (
            PricingPolicy.objects.filter(id=policy_id, policy_key__isnull=False)
            .values_list("scope", flat=True)
            .first()
        )
        if scope is None:
            reject(404, "NOT_FOUND", "Tariff revision not found")
        _scope_lock(scope)
        p = _get(policy_id, lock=True)
        _assert_version(p, version)
        if p.status != "DRAFT":
            reject(409, "POLICY_STATE_CONFLICT", "Only a draft can be published")
        if p.ends_at is not None and p.ends_at <= timezone.now():
            reject(409, "PRICING_WINDOW_EXPIRED", "Cannot publish an expired window")
        for other in PricingPolicy.objects.filter(status="PUBLISHED", scope=p.scope).exclude(
            id=p.id
        ):
            if overlap(p.starts_at, p.ends_at, other.starts_at, other.ends_at):
                reject(409, "PRICING_SCOPE_CONFLICT", "Overlapping published tariff window")
        p.status = "PUBLISHED"
        p.state_version += 1
        p.save(update_fields=["status", "state_version", "updated_at"])
        _audit(actor, p, "PUBLISH", reason)
    return p


def withdraw(actor, policy_id, version, reason):
    with transaction.atomic():
        scope = (
            PricingPolicy.objects.filter(id=policy_id, policy_key__isnull=False)
            .values_list("scope", flat=True)
            .first()
        )
        if scope is None:
            reject(404, "NOT_FOUND", "Tariff revision not found")
        _scope_lock(scope)
        p = _get(policy_id, lock=True)
        _assert_version(p, version)
        if p.status != "PUBLISHED":
            reject(409, "POLICY_STATE_CONFLICT", "Only published revisions may be withdrawn")
        p.status = "WITHDRAWN"
        p.state_version += 1
        p.save(update_fields=["status", "state_version", "updated_at"])
        _audit(actor, p, "WITHDRAW", reason)
    return p
