"""Version-attached travel-slab minimum for the EXISTING commercial tariff.

This is a pre-publication adjunct. The published PricingPolicy and original
cow-commercial-v1 evaluator remain the only charging rate/GST authority.
"""
from __future__ import annotations

from django.db import transaction
from cow.common.errors import reject
from cow.domain.booking_charge_budget import BookingBudgetError, validate_minimum_schedule
from cow.models import (AuditEvent, PricingPolicy, CoWMinimumChargingSchedule)
from cow.services import operational_eligibility as safety
from cow.services.charging_commercial_policy import stable_digest


def configure(*, actor, policy_id, inside_slabs, outside_slabs, authorization_ref):
    if type(authorization_ref) is not str or not 12<=len(authorization_ref)<=200:
        reject(422,'BOOKING_MINIMUM_AUTHORITY_MISSING','Approved policy reference required')
    try:
        validate_minimum_schedule(inside_slabs)
        if outside_slabs is not None:
            validate_minimum_schedule(outside_slabs)
    except BookingBudgetError as exc:
        reject(422,exc.code,'Malformed distance-linked minimum charging schedule')
    doc={'inside_slabs':inside_slabs,'outside_slabs':outside_slabs}
    digest=stable_digest(doc)
    with transaction.atomic():
        safety.fresh_admin(actor,'pricing.manage')
        safety.lock_gate()
        policy=PricingPolicy.objects.select_for_update().filter(pk=policy_id).first()
        if policy is None:
            reject(404,'BOOKING_TARIFF_NOT_FOUND','Tariff does not exist')
        if not hasattr(policy,'commercial_payload') or policy.commercial_payload.channel!='ROAMING':
            reject(409,'BOOKING_TARIFF_CHANNEL_NOT_SUPPORTED','Requires roaming commercial tariff')
        # Do not backfill an already-published tariff with invisible price terms.
        # Publish a NEW version containing this schedule before customers book.
        if policy.status!='DRAFT':
            reject(409,'BOOKING_TARIFF_IMMUTABLE','Only draft tariff versions may receive a schedule')
        previous=CoWMinimumChargingSchedule.objects.select_for_update().filter(policy=policy).first()
        if previous is not None:
            if previous.schedule_sha256!=digest or previous.authorization_ref!=authorization_ref:
                reject(409,'BOOKING_MINIMUM_SCHEDULE_CONFLICT','Draft schedule already defined differently')
            return {'id':str(previous.pk),'policy_id':str(policy.pk),'sha256':digest,'replayed':True}
        row=CoWMinimumChargingSchedule.objects.create(
            policy=policy,inside_slabs=inside_slabs,outside_slabs=outside_slabs,
            schedule_sha256=digest,authorized_by=actor,
            authorization_ref=authorization_ref)
        AuditEvent.objects.create(actor=actor,target_type='CoWMinimumChargingSchedule',
            target_id=row.pk,action='COW_TARIFF_MINIMUM_CONFIGURED',
            details={'policy_id':str(policy.pk),'sha256':digest,
                     'authorization_ref':authorization_ref})
        return {'id':str(row.pk),'policy_id':str(policy.pk),'sha256':digest,'replayed':False}


def view(actor, policy_id):
    from cow.services import rbac
    rbac.require(actor,'pricing.read')
    row=CoWMinimumChargingSchedule.objects.filter(policy_id=policy_id).first()
    if row is None:
        reject(404,'BOOKING_MINIMUM_NOT_CONFIGURED','Tariff has no minimum schedule')
    return {'id':str(row.pk),'policy_id':str(row.policy_id),
        'inside_slabs':row.inside_slabs,'outside_slabs':row.outside_slabs,
        'sha256':row.schedule_sha256,'authorization_ref':row.authorization_ref}
