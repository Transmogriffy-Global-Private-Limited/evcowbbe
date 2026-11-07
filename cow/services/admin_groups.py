"""Admin extensions for the existing pricing Group ledger (migration 0010).

Never treat a pricing Group as an RBAC role or notification audience.
Closing membership preserves historical rows, no deletes.
"""
from __future__ import annotations


from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from cow.common.errors import reject
from cow.models import (AuditEvent, CommercialPricingGroup,
                        CommercialGroupMembership, CommercialTariffAssignment,
                        CustomerProfile)
from cow.services import rbac
from cow.services.zone_admin import _actor, _reason


def _group(group_id, *, lock=False):
    qs = CommercialPricingGroup.objects.select_for_update() if lock else CommercialPricingGroup.objects
    group = qs.filter(pk=group_id).first()
    if group is None:
        reject(404, "GROUP_NOT_FOUND", "Pricing Group not found")
    return group


def _audit(actor, target, action, reason, **extra):
    AuditEvent.objects.create(actor=actor, target_type=type(target).__name__, target_id=target.pk,
                              action=action, details={"reason": _reason(reason), **extra})


def group_detail(actor, group_id):
    rbac.require(actor, "pricing.read")
    obj = _group(group_id)
    now = timezone.now()
    active_count = CommercialGroupMembership.objects.filter(group=obj, starts_at__lte=now).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    ).count()
    from cow.services.commercial_admin import group_view
    return {**group_view(obj), "active_member_count": active_count}


def group_members(actor, group_id, *, limit=50, before=None, include_history=False):
    rbac.require(actor, "pricing.read")
    rbac.require(actor, "users.read")
    group = _group(group_id)
    qs = CommercialGroupMembership.objects.filter(group=group).select_related(
        "customer", "customer__principal", "customer__principal__account"
    ).order_by("-starts_at", "-id")
    if not include_history:
        now = timezone.now()
        qs = qs.filter(starts_at__lte=now).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    if before:
        anchor = CommercialGroupMembership.objects.filter(group=group, pk=before).first()
        if anchor is None:
            reject(422, "INVALID_CURSOR", "Cursor does not belong to this Group")
        qs = qs.filter(Q(starts_at__lt=anchor.starts_at) | Q(starts_at=anchor.starts_at, id__lt=anchor.id))
    if not 1 <= limit <= 100:
        reject(422, "INVALID_PAGE_LIMIT", "Page limit must be 1..100")
    rows = list(qs[:limit+1]); more = len(rows)>limit; rows=rows[:limit]
    return {"items": [{"membership_id": str(x.pk), "customer_profile_id": str(x.customer_id),
                        "customer_id": str(x.customer.principal_id),
                        "email": x.customer.principal.account.email,
                        "full_name": x.customer.full_name,
                        "starts_at": x.starts_at.isoformat(),
                        "ends_at": x.ends_at.isoformat() if x.ends_at else None} for x in rows],
            "next_cursor": str(rows[-1].pk) if more and rows else None}


def remove_member(actor, customer_profile_id, reason):
    reason = _reason(reason)
    with transaction.atomic():
        actor = _actor(actor, "pricing.manage")
        customer = CustomerProfile.objects.select_for_update().filter(pk=customer_profile_id).first()
        if customer is None:
            reject(404, "CUSTOMER_NOT_FOUND", "Customer profile not found")
        now = timezone.now()
        memberships = list(CommercialGroupMembership.objects.select_for_update().filter(
            customer=customer, starts_at__lte=now).filter(
                Q(ends_at__isnull=True) | Q(ends_at__gt=now)))
        if len(memberships) > 1:
            reject(409, "MEMBERSHIP_CONFLICT", "Multiple active pricing Group memberships")
        if not memberships:
            return {"customer_profile_id": str(customer.pk), "removed": False, "already_absent": True}
        old = memberships[0]
        old.ends_at = now
        old.save(update_fields=["ends_at"])
        _audit(actor, old, "COMMERCIAL_GROUP_MEMBER_REMOVED", reason,
               customer_profile_id=str(customer.pk), group_id=str(old.group_id))
    return {"customer_profile_id": str(customer.pk), "removed": True, "already_absent": False}


def set_group_active(actor, group_id, enabled, reason):
    reason = _reason(reason)
    with transaction.atomic():
        actor = _actor(actor, "pricing.manage")
        obj = _group(group_id, lock=True)
        if obj.is_active == enabled:
            from cow.services.commercial_admin import group_view
            return group_view(obj)
        if not enabled:
            now = timezone.now()
            live_members = CommercialGroupMembership.objects.filter(group=obj).filter(
                Q(ends_at__isnull=True) | Q(ends_at__gt=now)).exists()
            assigned = CommercialTariffAssignment.objects.filter(group=obj).filter(
                Q(ends_at__isnull=True) | Q(ends_at__gt=now)).exists()
            if live_members or assigned:
                reject(409, "GROUP_IN_USE", "Remove memberships and close tariffs before deactivation")
        obj.is_active = enabled
        obj.save(update_fields=["is_active"])
        _audit(actor, obj, "COMMERCIAL_GROUP_ACTIVATED" if enabled else "COMMERCIAL_GROUP_DEACTIVATED", reason)
    from cow.services.commercial_admin import group_view
    return group_view(obj)
