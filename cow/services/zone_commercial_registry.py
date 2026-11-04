"""Read-only ORM-to-domain adapter, not an authorization or payment entrypoint.

Every caller MUST provide server-verified location evidence and a trusted
serviceability/override decision. No HTTP route is registered by this module.
The existing PricingPolicy is the ONLY publication and timing ledger.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from uuid import UUID

from django.db.models import Q

from cow.domain.zone_position import VerifiedPosition, radial_relation
from cow.domain.zone_tariff_convergence import (
    CommercialContractError, Evidence, PublishedZoneTariff,
    TrustedCommercialContext, ZoneGSTBinding, evaluate_zone_tariff,
    select_published_zone_plan, select_zone_gst, validate_tariff_plan,
)
from cow.models import (
    ChargerZoneAssignment, DutyZoneAssignment, PricingPolicy,
    ServiceZone, ZoneGSTAssignment, ZoneTariffPayload,
)


class ZoneResolutionUnavailable(CommercialContractError):
    pass


def _at(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ZoneResolutionUnavailable("Server-authorized aware decision instant required")
    return value.astimezone(timezone.utc)


def _window(qs, instant):
    return qs.filter(Q(starts_at__isnull=True) | Q(starts_at__lte=instant)).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=instant))


def _exact_one(qs, label):
    found = list(qs[:2])
    if len(found) != 1:
        raise ZoneResolutionUnavailable(f"Missing or ambiguous {label}")
    return found[0]


def _parse_uuid(value, label):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ZoneResolutionUnavailable(f"Invalid {label}") from None


def assigned_roaming_zone(duty_id, at):
    instant = _at(at)
    assignment = _exact_one(
        _window(DutyZoneAssignment.objects.select_related("zone", "duty"), instant).filter(duty_id=_parse_uuid(duty_id, "duty")),
        "driver-duty zone assignment",
    )
    if assignment.duty.ended_at is not None and assignment.duty.ended_at <= instant:
        raise ZoneResolutionUnavailable("Driver duty ended at decision instant")
    if assignment.duty.started_at > instant:
        raise ZoneResolutionUnavailable("Driver duty not started at decision instant")
    return _valid_zone(assignment.zone, instant), assignment


def assigned_qr_zone(charger_id, at):
    instant = _at(at)
    assignment = _exact_one(
        _window(ChargerZoneAssignment.objects.select_related("zone"), instant).filter(charger_id=_parse_uuid(charger_id, "charger")),
        "site/charger zone assignment",
    )
    return _valid_zone(assignment.zone, instant), assignment


def _valid_zone(zone, instant):
    if zone.status != "PUBLISHED" or (zone.starts_at is not None and zone.starts_at > instant) or (zone.ends_at is not None and instant >= zone.ends_at):
        raise ZoneResolutionUnavailable("Zone revision is not published/effective")
    if zone.version < 1:
        raise ZoneResolutionUnavailable("Invalid zone revision")
    return zone


def _context(zone, assignment, channel, at, position, outside_authorized, policy_decision_ref, *, max_location_age_seconds=300):
    instant = _at(at)
    if not isinstance(position, VerifiedPosition):
        raise ZoneResolutionUnavailable("Server-verified service position required")
    if type(max_location_age_seconds) is not int or not 1 <= max_location_age_seconds <= 86400:
        raise ZoneResolutionUnavailable("Invalid server-set location freshness limit")
    if position.observed_at > instant:
        raise ZoneResolutionUnavailable("Future location observation")
    if instant - position.observed_at > timedelta(seconds=max_location_age_seconds):
        raise ZoneResolutionUnavailable("Location observation is stale")
    # The provider must independently enforce a freshness/accuracy/ownership
    # contract. Do not turn an arbitrary stale position into pricing authority.
    if not isinstance(policy_decision_ref, str) or not policy_decision_ref.strip():
        raise ZoneResolutionUnavailable("Verified serviceability decision required")
    relation = radial_relation(zone.center_latitude, zone.center_longitude, zone.radius_m, position)
    if relation == "UNKNOWN":
        raise ZoneResolutionUnavailable("Zone membership uncertain")
    if relation == "OUTSIDE" and not outside_authorized:
        raise ZoneResolutionUnavailable("Outside service not authorized")
    if relation == "OUTSIDE" and zone.restriction_mode == "STRICT":
        # No unverified override string may confer monetary authority.
        # A future Admin override needs a durable, independently validated event.
        raise ZoneResolutionUnavailable("Strict zone outside override not yet integrated")
    provenance = f"{assignment.authorization_ref};{position.evidence_ref};{policy_decision_ref}"
    return TrustedCommercialContext(
        zone_id=zone.zone_key, channel=channel, relation=relation,
        decision_at=instant, provenance=provenance,
        driver_duty_id=str(assignment.duty_id) if channel == "ROAMING" else None,
        site_id=str(assignment.charger_id) if channel == "ON_SITE_QR" else None,
        outside_authorized=outside_authorized,
    )


def roaming_context(duty_id, at, position: VerifiedPosition, *,
                    outside_authorized=False, policy_decision_ref="",
                    duty_eligibility_ref="", max_location_age_seconds=300):
    """Internal-only read: dispatch must positively prove duty eligibility."""
    if not isinstance(duty_eligibility_ref, str) or not duty_eligibility_ref.strip():
        raise ZoneResolutionUnavailable("Current Driver/duty/assets eligibility proof required")
    zone, assignment = assigned_roaming_zone(duty_id, at)
    ctx = _context(zone, assignment, "ROAMING", at, position,
                              outside_authorized, policy_decision_ref,
                              max_location_age_seconds=max_location_age_seconds)
    return zone, TrustedCommercialContext(
        zone_id=ctx.zone_id, channel=ctx.channel, relation=ctx.relation,
        decision_at=ctx.decision_at, provenance=ctx.provenance + ";DUTY:" + duty_eligibility_ref,
        driver_duty_id=ctx.driver_duty_id, outside_authorized=ctx.outside_authorized,
    )


def qr_context(charger_id, at, position: VerifiedPosition, *, policy_decision_ref="",
               qr_proof_ref="", max_location_age_seconds=300):
    if not isinstance(qr_proof_ref, str) or not qr_proof_ref.strip():
        raise ZoneResolutionUnavailable("Verified QR token and charger proximity proof required")
    zone, assignment = assigned_qr_zone(charger_id, at)
    context = _context(zone, assignment, "ON_SITE_QR", at, position, False,
                       policy_decision_ref, max_location_age_seconds=max_location_age_seconds)
    if context.relation != "INSIDE":
        raise ZoneResolutionUnavailable("QR charging requires verified site proximity")
    # The upstream trusted QR verifier must validate charger-specific proximity,
    # token ownership, replay and time before supplying qr_proof_ref. The zone
    # radius here is NOT a substitute for the charger-specific proximity check.
    context = TrustedCommercialContext(zone_id=context.zone_id, channel=context.channel,
        relation=context.relation, decision_at=context.decision_at,
        provenance=context.provenance + ";QR:" + qr_proof_ref,
        site_id=context.site_id, outside_authorized=False)
    return zone, context


def published_plan(zone: ServiceZone, context: TrustedCommercialContext):
    if str(context.zone_id) != zone.zone_key:
        raise ZoneResolutionUnavailable("Zone context mismatch")
    scope = {"kind": "ZONE_" + context.channel, "key": zone.zone_key}
    published = list(PricingPolicy.objects.filter(status="PUBLISHED", policy_key__isnull=False, scope=scope))
    if not published:
        raise ZoneResolutionUnavailable("No published pricing revision for zone and channel")
    ids = [p.id for p in published]
    payloads = {p.policy_id: p for p in ZoneTariffPayload.objects.filter(policy_id__in=ids, zone=zone, channel=context.channel)}
    if len(payloads) != len(published):
        raise ZoneResolutionUnavailable("Published zone tariff lacks matching versioned payload")
    catalog = []
    for policy in published:
        payload = payloads[policy.id]
        if payload.plan.get("policy_id") != policy.policy_key or payload.plan.get("policy_version") != policy.policy_version:
            raise ZoneResolutionUnavailable("Tariff identity does not match publication ledger")
        validate_tariff_plan(payload.plan)
        catalog.append(PublishedZoneTariff(plan=payload.plan, starts_at=policy.starts_at, ends_at=policy.ends_at))
    return select_published_zone_plan(catalog, context)


def approved_zone_gst(zone: ServiceZone, context: TrustedCommercialContext):
    candidates = list(ZoneGSTAssignment.objects.filter(zone=zone, channel=context.channel, status="APPROVED"))
    bindings = [ZoneGSTBinding(
        zone_id=zone.zone_key, channel=x.channel, binding_version=x.binding_version,
        starts_at=x.starts_at, ends_at=x.ends_at, seller_tax_ref=x.seller_tax_ref,
        legal_decision_ref=x.legal_decision_ref, tax_profiles=x.tax_profiles,
    ) for x in candidates]
    return select_zone_gst(bindings, zone.zone_key, context.channel, context.decision_at)


def preview_upfront(zone: ServiceZone, context: TrustedCommercialContext, evidence: Evidence):
    """Resolve current DB policy and GST for tentative UPFRONT evaluation ONLY.

    Customer commitment requires verified Driver/QR authorization and a frozen
    returned snapshot. This function NEVER finalizes a job or posts money.
    """
    return evaluate_zone_tariff(
        published_plan(zone, context), context, evidence, "UPFRONT",
        approved_zone_gst(zone, context),
    )


def evaluate_frozen(plan, context: TrustedCommercialContext, evidence: Evidence,
                    phase: str, gst: ZoneGSTBinding):
    """Use a previously frozen, authorized plan+tax binding, never current DB.

    The caller must rehydrate these from a durable customer commitment record
    and provide verified final route/HAL facts. This is still pure arithmetic,
    NOT a payment/financial-settlement entrypoint.
    """
    if phase not in {"COMPLETED", "CANCELLED", "NO_SHOW"}:
        raise ZoneResolutionUnavailable("Only final outcomes use frozen evaluation")
    if not isinstance(gst, ZoneGSTBinding):
        raise ZoneResolutionUnavailable("Versioned frozen Zone GST decision required")
    return evaluate_zone_tariff(plan, context, evidence, phase, gst)
