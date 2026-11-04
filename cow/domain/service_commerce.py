"""Versioned serviceability, route-tariff and wallet-settlement decisions.

Pure business calculations only. Persistence, authoritative route and position
sources, authentication, rate publication and ledger transactions are mandatory
adapters. This module MUST NOT by itself activate real money or charging.
"""

from __future__ import annotations

import hmac
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from hashlib import sha256
from typing import Literal


class DecisionError(ValueError):
    """Fail-closed policy/configuration/admission error with stable code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise DecisionError(code)


def utc(value: datetime) -> datetime:
    require(isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() is not None, "OFFSET_REQUIRED")
    return value.astimezone(timezone.utc)


def minor(value: int) -> int:
    require(type(value) is int and 0 <= value <= 2**63-1, "INVALID_MINOR_AMOUNT")
    return value


def safe_add(*parts: int) -> int:
    for part in parts:
        minor(part)
    return minor(sum(parts))


@dataclass(frozen=True)
class Point:
    latitude: Decimal
    longitude: Decimal

    def __post_init__(self):
        for name, limit in (("latitude", 90), ("longitude", 180)):
            n = getattr(self, name)
            require(isinstance(n, Decimal) and n.is_finite(), "INVALID_LOCATION")
            require(-limit <= n <= limit, "INVALID_LOCATION")


def great_circle_metres(a: Point, b: Point) -> float:
    """Geometry/service boundary only, not chargeable road distance."""
    lat1, lon1, lat2, lon2 = map(math.radians,
        [float(a.latitude), float(a.longitude), float(b.latitude), float(b.longitude)])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    hav = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * 6371008.8 * math.asin(min(1., math.sqrt(max(0., hav))))


class BoundaryMode(str, Enum):
    STRICT = "STRICT"            # Default: reject outside location
    ADVISORY = "ADVISORY"        # Permit, flag, and select outside tariff


@dataclass(frozen=True)
class ServiceZone:
    zone_id: str
    version: int
    centre: Point
    radius_m: int
    mode: BoundaryMode = BoundaryMode.STRICT

    def __post_init__(self):
        require(bool(self.zone_id.strip()) and self.version >= 1, "INVALID_ZONE_IDENTITY")
        require(type(self.radius_m) is int and self.radius_m > 0, "INVALID_ZONE_RADIUS")
        require(isinstance(self.mode, BoundaryMode), "INVALID_ZONE_MODE")


@dataclass(frozen=True)
class ZoneDecision:
    zone_id: str
    version: int
    within_zone: bool
    permitted: bool
    advisory_alert: bool
    distance_from_centre_m: float
    pricing_side: Literal["INSIDE", "OUTSIDE"]


def evaluate_zone(zone: ServiceZone, customer_location: Point) -> ZoneDecision:
    d = great_circle_metres(zone.centre, customer_location)
    inside = d <= zone.radius_m
    return ZoneDecision(zone.zone_id, zone.version, inside,
        inside or zone.mode == BoundaryMode.ADVISORY,
        not inside and zone.mode == BoundaryMode.ADVISORY,
        d, "INSIDE" if inside else "OUTSIDE")


class OriginPolicy(str, Enum):
    DRIVER_CURRENT = "DRIVER_CURRENT"  # DEFAULT: never zone centre
    ZONE_CENTRE = "ZONE_CENTRE"      # Explicit Admin override only


class TariffStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    WITHDRAWN = "WITHDRAWN"


@dataclass(frozen=True)
class DriverZoneAssignment:
    """Server-owned assignment, not permission to restrict Driver movement.

    The zone ID stays stable across published geometry revisions. Quote snapshots
    pin the selected zone version and this assignment's identity.
    """

    assignment_id: str
    driver_id: str
    zone_id: str
    active_from: datetime
    active_until: datetime | None = None
    primary: bool = False

    def __post_init__(self):
        require(all(isinstance(v, str) and v.strip() for v in
                    (self.assignment_id, self.driver_id, self.zone_id)),
                "INVALID_DRIVER_ZONE_ASSIGNMENT")
        require(type(self.primary) is bool, "INVALID_PRIMARY_ASSIGNMENT")
        start = utc(self.active_from)
        if self.active_until is not None:
            require(utc(self.active_until) > start, "INVALID_ASSIGNMENT_WINDOW")

    def active_at(self, now: datetime) -> bool:
        t = utc(now)
        return (utc(self.active_from) <= t and
                (self.active_until is None or t < utc(self.active_until)))


def select_driver_zone_assignment(driver_id: str,
                                  assignments: tuple[DriverZoneAssignment, ...],
                                  now: datetime, *,
                                  selected_assignment_id: str | None = None,
                                  ) -> DriverZoneAssignment:
    """Choose from server-verified assignments, NEVER customer zone lookup.

    A Driver may have more than one zone; the coordinator must choose a known
    assignment or explicitly set one primary. Otherwise pricing fails closed.
    """
    require(isinstance(driver_id, str) and bool(driver_id.strip()), "DRIVER_ID_REQUIRED")
    now = utc(now)
    active = [a for a in assignments if a.driver_id == driver_id and a.active_at(now)]
    require(bool(active), "DRIVER_NOT_ASSIGNED_TO_ACTIVE_ZONE")
    require(len({a.assignment_id for a in active}) == len(active), "DUPLICATE_ASSIGNMENT_ID")
    require(len({a.zone_id for a in active}) == len(active), "DUPLICATE_ACTIVE_DRIVER_ZONE")
    if selected_assignment_id is not None:
        matches = [a for a in active if a.assignment_id == selected_assignment_id]
        require(len(matches) == 1, "DRIVER_ZONE_SELECTION_INVALID")
        return matches[0]
    primary = [a for a in active if a.primary]
    require(len(primary) <= 1, "MULTIPLE_PRIMARY_DRIVER_ZONES")
    if primary:
        return primary[0]
    require(len(active) == 1, "DRIVER_ZONE_SELECTION_REQUIRED")
    return active[0]




@dataclass(frozen=True)
class RouteEvidence:
    provider: str
    route_ref: str
    origin: Point
    destination: Point
    distance_m: int
    duration_s: int | None
    observed_at: datetime
    expires_at: datetime
    origin_policy: OriginPolicy
    driver_id: str | None

    def __post_init__(self):
        require(bool(self.provider.strip()) and bool(self.route_ref.strip()), "MISSING_ROUTE_PROVENANCE")
        require(type(self.distance_m) is int and self.distance_m >= 0, "INVALID_ROUTE_DISTANCE")
        require(self.duration_s is None or (type(self.duration_s) is int and self.duration_s >= 0), "INVALID_ROUTE_DURATION")
        require(isinstance(self.origin_policy, OriginPolicy), "INVALID_ROUTE_ORIGIN_POLICY")
        require(utc(self.expires_at) > utc(self.observed_at), "INVALID_ROUTE_EXPIRY")
        if self.origin_policy == OriginPolicy.DRIVER_CURRENT:
            require(bool(self.driver_id), "DRIVER_LOCATION_REQUIRED")
        else:
            require(self.driver_id is None, "CENTRE_ROUTE_DRIVER_MISMATCH")

    def check(self, now: datetime, customer_location: Point):
        t = utc(now)
        require(utc(self.observed_at) <= t < utc(self.expires_at), "STALE_OR_FUTURE_ROUTE")
        require(self.destination == customer_location, "ROUTE_DESTINATION_CHANGED")
        require(self.provider != "haversine", "ROAD_ROUTE_REQUIRED")


@dataclass(frozen=True)
class DistanceSlab:
    from_m: int
    to_m_exclusive: int | None
    transport_minor: int
    minimum_minor: int

    def __post_init__(self):
        require(type(self.from_m) is int and self.from_m >= 0, "INVALID_SLAB_START")
        require(self.to_m_exclusive is None or (type(self.to_m_exclusive) is int
                and self.to_m_exclusive > self.from_m), "INVALID_SLAB_END")
        minor(self.transport_minor)
        minor(self.minimum_minor)

    def contains(self, m: int) -> bool:
        return self.from_m <= m and (self.to_m_exclusive is None or m < self.to_m_exclusive)


class MinimumTreatment(str, Enum):
    SPEND_FLOOR = "SPEND_FLOOR"
    REFUNDABLE_ADVANCE = "REFUNDABLE_ADVANCE"


def validate_slabs(slabs: tuple[DistanceSlab, ...]) -> None:
    require(bool(slabs), "NO_TARIFF_SLABS")
    expected = 0
    for i, slab in enumerate(slabs):
        require(slab.from_m == expected, "NONCONTIGUOUS_TARIFF_SLABS")
        if slab.to_m_exclusive is None:
            require(i == len(slabs) - 1, "OPEN_SLAB_NOT_LAST")
        else:
            expected = slab.to_m_exclusive
    require(slabs[-1].to_m_exclusive is None, "TARIFF_SLABS_NOT_TOTAL")


@dataclass(frozen=True)
class Tariff:
    policy_id: str
    revision: int
    valid_from: datetime
    valid_until: datetime | None
    zone_id: str
    zone_version: int
    inside: tuple[DistanceSlab, ...]
    outside: tuple[DistanceSlab, ...] = ()
    minimum_treatment: MinimumTreatment = MinimumTreatment.SPEND_FLOOR
    route_origin: OriginPolicy = OriginPolicy.DRIVER_CURRENT
    currency: str = "INR"
    status: TariffStatus = TariffStatus.DRAFT

    def __post_init__(self):
        require(isinstance(self.status, TariffStatus), "INVALID_TARIFF_STATUS")
        require(bool(self.policy_id.strip()) and self.revision > 0, "INVALID_TARIFF_IDENTITY")
        require(self.zone_version >= 1 and bool(self.zone_id.strip()), "INVALID_TARIFF_ZONE")
        require(re.fullmatch(r"[A-Z]{3}", self.currency) is not None, "INVALID_CURRENCY")
        require(isinstance(self.route_origin, OriginPolicy), "INVALID_ROUTE_ORIGIN_POLICY")
        require(isinstance(self.minimum_treatment, MinimumTreatment), "INVALID_MINIMUM_TREATMENT")
        if self.valid_until is not None:
            require(utc(self.valid_until) > utc(self.valid_from), "INVALID_TARIFF_WINDOW")
        else:
            utc(self.valid_from)
        validate_slabs(self.inside)
        if self.outside:
            validate_slabs(self.outside)


def select_published_zone_tariff(zone: ServiceZone, tariffs: tuple[Tariff, ...],
                                 now: datetime) -> Tariff:
    """Only an effective, published tariff scoped to this exact zone revision.

    Never fall back to a platform/global or neighbouring zone rate. Conflicting
    published revisions are a policy publication error, not a tie-break on price.
    """
    now = utc(now)
    matches = [t for t in tariffs if t.zone_id == zone.zone_id
               and t.zone_version == zone.version and t.status == TariffStatus.PUBLISHED
               and utc(t.valid_from) <= now and
               (t.valid_until is None or now < utc(t.valid_until))]
    require(bool(matches), "ASSIGNED_ZONE_TARIFF_NOT_PUBLISHED")
    require(len(matches) == 1, "AMBIGUOUS_PUBLISHED_ZONE_TARIFF")
    return matches[0]


@dataclass(frozen=True)
class UpfrontQuote:
    policy_id: str
    policy_revision: int
    zone_id: str
    zone_version: int
    driver_assignment_id: str
    within_zone: bool
    route_provider: str
    route_ref: str
    distance_m: int
    route_origin: OriginPolicy
    quoted_driver_id: str | None
    transport_minor: int
    minimum_minor: int
    upfront_minor: int
    minimum_treatment: MinimumTreatment
    currency: str
    quoted_at: datetime
    quote_expires_at: datetime


def quote_mobile(zone: ServiceZone, tariff: Tariff, route: RouteEvidence,
                 customer_location: Point, now: datetime, *,
                 assignment: DriverZoneAssignment, quote_lifetime_s: int = 120) -> UpfrontQuote:
    """Quote against an assigned Driver's own zone tariff, not customer zones."""
    now = utc(now)
    require(type(quote_lifetime_s) is int and 1 <= quote_lifetime_s <= 3600, "INVALID_QUOTE_TTL")
    require(isinstance(assignment, DriverZoneAssignment) and assignment.active_at(now),
            "DRIVER_ZONE_ASSIGNMENT_INACTIVE")
    require(assignment.zone_id == zone.zone_id, "DRIVER_ZONE_TARIFF_MISMATCH")
    require(tariff.status == TariffStatus.PUBLISHED, "TARIFF_NOT_PUBLISHED")
    require(zone.zone_id == tariff.zone_id and zone.version == tariff.zone_version, "TARIFF_ZONE_DRIFT")
    decision = evaluate_zone(zone, customer_location)
    require(decision.permitted, "OUTSIDE_STRICT_ZONE")
    require(tariff.route_origin == route.origin_policy, "ROUTE_ORIGIN_POLICY_MISMATCH")
    route.check(now, customer_location)
    require(utc(tariff.valid_from) <= now and
            (tariff.valid_until is None or now < utc(tariff.valid_until)), "TARIFF_NOT_EFFECTIVE")
    if tariff.route_origin == OriginPolicy.ZONE_CENTRE:
        require(route.origin == zone.centre, "ROUTE_ORIGIN_NOT_CENTRE")
    else:
        require(route.driver_id == assignment.driver_id, "ROUTE_DRIVER_ASSIGNMENT_MISMATCH")
    slabs = tariff.inside if decision.within_zone else tariff.outside
    require(bool(slabs), "OUTSIDE_TARIFF_NOT_CONFIGURED")
    slab = next((s for s in slabs if s.contains(route.distance_m)), None)
    require(slab is not None, "NO_MATCHING_DISTANCE_SLAB")
    expiry = min(utc(route.expires_at), now + timedelta(seconds=quote_lifetime_s))
    if tariff.valid_until:
        expiry = min(expiry, utc(tariff.valid_until))
    if assignment.active_until:
        expiry = min(expiry, utc(assignment.active_until))
    require(expiry > now, "QUOTE_ALREADY_EXPIRED")
    return UpfrontQuote(tariff.policy_id, tariff.revision, zone.zone_id, zone.version,
        assignment.assignment_id, decision.within_zone, route.provider, route.route_ref,
        route.distance_m, route.origin_policy, assignment.driver_id,
        slab.transport_minor, slab.minimum_minor,
        safe_add(slab.transport_minor, slab.minimum_minor), tariff.minimum_treatment,
        tariff.currency, now, expiry)


def quote_mobile_for_driver(*, driver_id: str,
                            assignments: tuple[DriverZoneAssignment, ...],
                            zones: tuple[ServiceZone, ...],
                            tariffs: tuple[Tariff, ...],
                            route: RouteEvidence,
                            customer_location: Point, now: datetime,
                            selected_assignment_id: str | None = None,
                            quote_lifetime_s: int = 120) -> UpfrontQuote:
    """Complete deterministic selection from server-owned policy snapshots.

    A customer in zone B with a Driver assigned to A uses A's INSIDE or OUTSIDE
    policy, never B's price. No global tariff or geographical price shopping.
    """
    now = utc(now)
    assignment = select_driver_zone_assignment(driver_id, assignments, now,
        selected_assignment_id=selected_assignment_id)
    matching = [z for z in zones if z.zone_id == assignment.zone_id]
    require(bool(matching), "ASSIGNED_ZONE_UNAVAILABLE")
    require(len(matching) == 1, "ASSIGNED_ZONE_VERSION_AMBIGUOUS")
    zone = matching[0]
    tariff = select_published_zone_tariff(zone, tariffs, now)
    return quote_mobile(zone, tariff, route, customer_location, now,
        assignment=assignment, quote_lifetime_s=quote_lifetime_s)


class BalanceEffect(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"
    NONE = "NONE"


@dataclass(frozen=True)
class SettlementDecision:
    due_minor: int
    already_debited_minor: int
    delta_minor: int
    effect: BalanceEffect
    amount_minor: int
    requires_topup: bool


def success_settlement(quote: UpfrontQuote, *, verified_charging_minor: int,
                       posted_upfront_minor: int, available_minor: int) -> SettlementDecision:
    """One FINAL incremental posting, never periodic meter-value debits.

    Caller must atomically persist by unique (transaction_id, 'FINAL') and
    account-lock/check balance. This pure method does NOT move funds.
    """
    energy = minor(verified_charging_minor)
    require(posted_upfront_minor == quote.upfront_minor, "UPFRONT_LEDGER_MISMATCH")
    avail = minor(available_minor)
    due_charging = (max(energy, quote.minimum_minor)
                    if quote.minimum_treatment == MinimumTreatment.SPEND_FLOOR else energy)
    due = safe_add(quote.transport_minor, due_charging)
    delta = due - posted_upfront_minor
    effect = BalanceEffect.DEBIT if delta > 0 else (BalanceEffect.CREDIT if delta < 0 else BalanceEffect.NONE)
    return SettlementDecision(due, posted_upfront_minor, delta, effect, abs(delta), delta > avail)


class CancelActor(str, Enum):
    CUSTOMER = "CUSTOMER"
    DRIVER = "DRIVER"
    PLATFORM = "PLATFORM"


class CancelPhase(str, Enum):
    BEFORE_ACCEPTANCE = "BEFORE_ACCEPTANCE"
    EN_ROUTE = "EN_ROUTE"
    ARRIVED = "ARRIVED"
    CHARGING_STARTED = "CHARGING_STARTED"


class RefundRule(str, Enum):
    FULL = "FULL"
    NONE = "NONE"
    TRAVEL_PROPORTIONAL = "TRAVEL_PROPORTIONAL"


@dataclass(frozen=True)
class CancellationPolicy:
    policy_id: str
    revision: int
    actor: CancelActor
    phase: CancelPhase
    rule: RefundRule
    # For travel-proportional fees: per kilometre, rounded half-up to 1 paise,
    # capped by transport already paid. A trusted, post-acceptance trace is required.
    travelled_rate_minor_per_km: Decimal = Decimal("0")
    max_retained_minor: int | None = None

    def __post_init__(self):
        require(bool(self.policy_id.strip()) and self.revision > 0, "INVALID_CANCELLATION_POLICY")
        require(isinstance(self.actor, CancelActor) and isinstance(self.phase, CancelPhase)
                and isinstance(self.rule, RefundRule), "INVALID_CANCELLATION_ENUM")
        require(isinstance(self.travelled_rate_minor_per_km, Decimal)
                and self.travelled_rate_minor_per_km.is_finite()
                and self.travelled_rate_minor_per_km >= 0, "INVALID_TRAVEL_RATE")
        if self.max_retained_minor is not None:
            minor(self.max_retained_minor)
        if self.rule == RefundRule.TRAVEL_PROPORTIONAL:
            require(self.phase != CancelPhase.BEFORE_ACCEPTANCE, "TRAVEL_BEFORE_ACCEPTANCE_INVALID")
        else:
            require(self.travelled_rate_minor_per_km == 0, "UNUSED_TRAVEL_RATE")


@dataclass(frozen=True)
class CancellationDecision:
    retained_minor: int
    wallet_refund_minor: int
    reason: str
    evidence_required: bool


def cancel_settlement(quote: UpfrontQuote, policy: CancellationPolicy, *,
                      actor: CancelActor, phase: CancelPhase,
                      posted_upfront_minor: int, travelled_m: int | None = None,
                      verified_travel_evidence: bool = False) -> CancellationDecision:
    require(policy.actor == actor and policy.phase == phase, "CANCELLATION_POLICY_MISMATCH")
    require(posted_upfront_minor == quote.upfront_minor, "UPFRONT_LEDGER_MISMATCH")
    if phase == CancelPhase.CHARGING_STARTED:
        raise DecisionError("ACTIVE_CHARGING_REQUIRES_STOP_AND_RECONCILIATION")
    if policy.rule == RefundRule.FULL:
        retained = 0
    elif policy.rule == RefundRule.NONE:
        retained = posted_upfront_minor
    else:
        require(verified_travel_evidence and type(travelled_m) is int
                and travelled_m >= 0, "TRAVEL_EVIDENCE_REQUIRED")
        retained = int((Decimal(travelled_m) / Decimal(1000)
                        * policy.travelled_rate_minor_per_km)
                       .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        retained = min(retained, quote.transport_minor)
    if policy.max_retained_minor is not None:
        retained = min(retained, policy.max_retained_minor)
    return CancellationDecision(retained, posted_upfront_minor - retained,
        f"{actor.value}:{phase.value}:{policy.rule.value}",
        policy.rule == RefundRule.TRAVEL_PROPORTIONAL)


class ChargingMode(str, Enum):
    MOBILE_REQUEST = "MOBILE_REQUEST"
    WALK_UP_QR = "WALK_UP_QR"


class QrLocationBasis(str, Enum):
    REQUEST_ADDRESS = "REQUEST_ADDRESS"
    CURRENT_CUSTOMER = "CURRENT_CUSTOMER"


@dataclass(frozen=True)
class GeoEvidence:
    point: Point
    observed_at: datetime
    accuracy_m: Decimal
    source: str
    trusted: bool

    def __post_init__(self):
        require(isinstance(self.accuracy_m, Decimal) and self.accuracy_m.is_finite()
                and self.accuracy_m >= 0, "INVALID_GEO_ACCURACY")
        require(bool(self.source.strip()), "MISSING_GEO_SOURCE")
        utc(self.observed_at)


@dataclass(frozen=True)
class QrStartPolicy:
    radius_m: int
    location_basis: QrLocationBasis = QrLocationBasis.REQUEST_ADDRESS
    max_age_seconds: int = 60
    max_accuracy_m: int = 30

    def __post_init__(self):
        require(type(self.radius_m) is int and self.radius_m > 0, "INVALID_QR_RADIUS")
        require(type(self.max_age_seconds) is int and self.max_age_seconds > 0, "INVALID_QR_FRESHNESS")
        require(type(self.max_accuracy_m) is int and self.max_accuracy_m > 0, "INVALID_QR_ACCURACY_LIMIT")


def authorize_qr_start(policy: QrStartPolicy, *, now: datetime, target: Point,
                       customer_observation: GeoEvidence, charger_observation: GeoEvidence,
                       server_challenge_verified: bool, charger_connector_verified: bool,
                       current_customer_consent: bool = False) -> None:
    """Raises on failure. No physical start; HAL command is a separate step.

    Both observations MUST be independently attested by trusted adapters.
    Phone GPS/QR strings supplied by a client cannot establish `trusted=True`.
    """
    now = utc(now)
    require(server_challenge_verified and charger_connector_verified, "QR_CLAIM_UNVERIFIED")
    require(policy.location_basis != QrLocationBasis.CURRENT_CUSTOMER
            or current_customer_consent, "CURRENT_LOCATION_CONSENT_REQUIRED")
    for e in (customer_observation, charger_observation):
        require(e.trusted, "GEO_EVIDENCE_UNTRUSTED")
        age = (now - utc(e.observed_at)).total_seconds()
        require(0 <= age <= policy.max_age_seconds, "GEO_EVIDENCE_STALE_OR_FUTURE")
        require(e.accuracy_m <= policy.max_accuracy_m, "GEO_EVIDENCE_TOO_INACCURATE")
        # Conservative uncertainty handling: if uncertainty spans past edge,
        # seek another reading rather than claiming presence.
        require(great_circle_metres(target, e.point) + float(e.accuracy_m)
                <= policy.radius_m, "QR_OUTSIDE_ALLOWED_LOCATION")


def verify_razorpay_webhook(raw_body: bytes, signature_hex: str, secret: bytes) -> bool:
    """Raw-body HMAC only. Persistence/dedup and payment capture are separate."""
    if not isinstance(raw_body, bytes) or not isinstance(secret, bytes) or not secret:
        return False
    if not isinstance(signature_hex, str) or re.fullmatch(r"[0-9a-fA-F]{64}", signature_hex) is None:
        return False
    expected = hmac.new(secret, raw_body, sha256).hexdigest()
    return hmac.compare_digest(expected, signature_hex.lower())


@dataclass(frozen=True)
class DispatchPriceCheck:
    accepted: bool
    reason: str


def check_quoted_driver(quote: UpfrontQuote, *, accepting_driver_id: str,
                        now: datetime, active_assignment: DriverZoneAssignment,
                        ) -> DispatchPriceCheck:
    """Driver acceptance never silently changes the already-debited quote.

    Another driver requires explicit re-quotation/re-authorization or a
    separately approved, audited platform-funded subsidy. Neither is inferred.
    """
    require(bool(accepting_driver_id), "DRIVER_ID_REQUIRED")
    if utc(now) < utc(quote.quoted_at):
        return DispatchPriceCheck(False, "QUOTE_NOT_YET_ACTIVE")
    if utc(now) >= utc(quote.quote_expires_at):
        return DispatchPriceCheck(False, "QUOTE_EXPIRED")
    if accepting_driver_id != quote.quoted_driver_id:
        return DispatchPriceCheck(False, "DRIVER_CHANGED_REQUOTE_REQUIRED")
    if (not isinstance(active_assignment, DriverZoneAssignment) or
            active_assignment.assignment_id != quote.driver_assignment_id or
            active_assignment.driver_id != quote.quoted_driver_id or
            active_assignment.zone_id != quote.zone_id or
            not active_assignment.active_at(now)):
        return DispatchPriceCheck(False, "DRIVER_ZONE_ASSIGNMENT_CHANGED")
    return DispatchPriceCheck(True, "QUOTED_BASIS_PRESERVED")


@dataclass(frozen=True)
class DriverPenaltyPolicy:
    policy_id: str
    revision: int
    flat_minor: int = 0
    travel_rate_minor_per_km: Decimal = Decimal("0")
    max_penalty_minor: int = 0
    active: bool = False

    def __post_init__(self):
        require(bool(self.policy_id.strip()) and self.revision > 0, "INVALID_PENALTY_POLICY")
        minor(self.flat_minor)
        minor(self.max_penalty_minor)
        require(isinstance(self.travel_rate_minor_per_km, Decimal)
                and self.travel_rate_minor_per_km.is_finite()
                and self.travel_rate_minor_per_km >= 0, "INVALID_PENALTY_RATE")


def assess_driver_penalty(policy: DriverPenaltyPolicy, *, cancellation_by_driver: bool,
                          liability_confirmed: bool, travel_m: int | None) -> int:
    """Assessment only; no payroll deduction or wallet transfer is authorized."""
    if not policy.active or not cancellation_by_driver:
        return 0
    require(liability_confirmed, "DRIVER_PENALTY_REVIEW_REQUIRED")
    require(type(travel_m) is int and travel_m >= 0, "DRIVER_PENALTY_TRAVEL_EVIDENCE_REQUIRED")
    extra = int((Decimal(travel_m) / Decimal(1000) * policy.travel_rate_minor_per_km)
                .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return min(safe_add(policy.flat_minor, extra), policy.max_penalty_minor)
