"""Immutable zone-owned composite-tariff adapter for the existing Django pricing engine.

This is an additive domain module. It does NOT write wallets, resolve arbitrary
client scopes, assign legal GST solely by geography, or send HAL commands.
Production callers MUST supply trusted, frozen policy, driver/site context,
approved zone-GST binding, and authoritative measurement provenance.

The existing cow.services.pricing_engine remains the single arithmetic engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID
import copy
import re

_ID = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_RATE = re.compile(r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")
_PHASES = frozenset({"UPFRONT", "COMPLETED", "CANCELLED", "NO_SHOW"})
_PRIMARY = frozenset({"ENERGY_KWH", "TIME_MINUTE", "SESSION"})
_RELATIONS = frozenset({"INSIDE", "OUTSIDE"})
_MODES = frozenset({"ROAMING", "ON_SITE_QR"})
_ROUNDING = frozenset({"CEIL_STARTED_MINUTE", "FLOOR_COMPLETED_MINUTE"})
_MAX_QUANTITY = 10**12
_MAX_MINOR = (1 << 63) - 1


class CommercialContractError(ValueError):
    """Caller/configuration/evidence failed a domain precondition; no charge authorized."""


class TaxBindingUnavailable(CommercialContractError):
    """No unique approved, applicable zone-GST decision exists."""


def _exact_keys(obj: Any, allowed: set[str], required: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(obj, dict) or not required.issubset(obj) or set(obj) - allowed:
        raise CommercialContractError(f"Invalid {label} fields")
    return obj


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CommercialContractError(f"Invalid {name}")
    return value


def _key(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise CommercialContractError(f"Invalid {name}")
    return value


def _money(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _RATE.fullmatch(value):
        raise CommercialContractError(f"Invalid exact decimal rate: {name}")
    return value


def _int(value: Any, name: str, maximum: int = _MAX_QUANTITY) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise CommercialContractError(f"Invalid nonnegative integer: {name}")
    return value


def _aware(at: Any) -> datetime:
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise CommercialContractError("Timezone-aware decision timestamp required")
    return at.astimezone(timezone.utc)


def _events(raw: Any, name: str) -> frozenset[str]:
    if not isinstance(raw, list) or not raw or any(not isinstance(v, str) or v not in _PHASES for v in raw) or len(raw) != len(set(raw)):
        raise CommercialContractError(f"Invalid event list: {name}")
    return frozenset(raw)


def _quantity(value: int, divisor: int = 1) -> str:
    # Divisor 1000 (meters -> km, Wh -> kWh) yields <=3 decimal digits.
    amount = Decimal(value) / Decimal(divisor)
    return format(amount, 'f')


def _trusted_source(sources: Mapping[str, str], field: str) -> str:
    source = sources.get(field)
    if not isinstance(source, str) or not source.strip() or len(source) > 200:
        raise CommercialContractError(f"Trusted evidence source is required: {field}")
    return source


def _minute_quantity(seconds: int, rounding: str) -> str:
    _int(seconds, "duration_seconds")
    if rounding == "CEIL_STARTED_MINUTE":
        return str((seconds + 59) // 60)
    if rounding == "FLOOR_COMPLETED_MINUTE":
        return str(seconds // 60)
    raise CommercialContractError("Explicit supported minute rounding required")


@dataclass(frozen=True)
class TrustedCommercialContext:
    """Server-derived context, NEVER directly deserialized from request JSON."""
    zone_id: str
    channel: str
    relation: str
    decision_at: datetime
    provenance: str
    driver_duty_id: str | None = None
    site_id: str | None = None
    outside_authorized: bool = False

    def __post_init__(self) -> None:
        _key(self.zone_id, "zone_id")
        if not isinstance(self.channel, str) or self.channel not in _MODES or not isinstance(self.relation, str) or self.relation not in _RELATIONS:
            raise CommercialContractError("Invalid commercial context kind")
        _aware(self.decision_at)
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise CommercialContractError("Trusted context provenance required")
        if self.channel == "ROAMING" and not self.driver_duty_id:
            raise CommercialContractError("Roaming tariff requires committed driver duty/zone")
        if self.channel == "ON_SITE_QR" and (not self.site_id or self.driver_duty_id):
            raise CommercialContractError("QR tariff requires site/charger context, not driver duty")
        if type(self.outside_authorized) is not bool:
            raise CommercialContractError("Outside authorization must be a trusted boolean")
        if self.relation == "OUTSIDE" and not self.outside_authorized:
            raise CommercialContractError("Outside-zone service requires explicit eligibility decision")


@dataclass(frozen=True)
class Evidence:
    """Measurements and their source identities are separate from customer input."""
    energy_wh: int | None = None
    charging_seconds: int | None = None
    travel_m: int | None = None
    waiting_seconds: int | None = None
    sources: Mapping[str, str] | None = None
    estimated: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("energy_wh", "charging_seconds", "travel_m", "waiting_seconds"):
            val = getattr(self, name)
            if val is not None:
                _int(val, name)
        if not isinstance(self.estimated, frozenset) or self.estimated - {"energy_wh", "charging_seconds", "travel_m", "waiting_seconds"}:
            raise CommercialContractError("Invalid estimate fields")
        if self.sources is not None and not isinstance(self.sources, dict):
            raise CommercialContractError("Sources must be a frozen caller-owned dictionary")


@dataclass(frozen=True)
class ZoneGSTBinding:
    """Zone -> explicitly approved tax decision, not zone-coordinate tax inference."""
    zone_id: str
    channel: str
    binding_version: int
    starts_at: datetime | None
    ends_at: datetime | None
    seller_tax_ref: str
    legal_decision_ref: str
    tax_profiles: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        _key(self.zone_id, "zone GST binding zone")
        if not isinstance(self.channel, str) or self.channel not in _MODES:
            raise TaxBindingUnavailable("A zone tax binding must identify a service channel")
        _int(self.binding_version, "binding version")
        if self.binding_version == 0 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in (self.seller_tax_ref, self.legal_decision_ref)):
            raise TaxBindingUnavailable("Approved seller and legal tax decision references required")
        start = _aware(self.starts_at) if self.starts_at is not None else None
        end = _aware(self.ends_at) if self.ends_at is not None else None
        if end is not None and (start is None or start >= end):
            raise TaxBindingUnavailable("Invalid tax binding window")
        if not isinstance(self.tax_profiles, dict) or not self.tax_profiles:
            raise TaxBindingUnavailable("Zone must have explicit tax profiles")
        for name, p in self.tax_profiles.items():
            _id(name, "tax class")
            _exact_keys(p, {"approved", "sgst_percent", "cgst_percent", "igst_percent"}, {"approved", "sgst_percent", "cgst_percent", "igst_percent"}, "zone GST profile")
            if p["approved"] is not True:
                raise TaxBindingUnavailable("Unapproved GST profile")
            rates = [Decimal(_money(p[k], k)) for k in ("sgst_percent", "cgst_percent", "igst_percent")]
            if any(x > 100 for x in rates) or (rates[2] and (rates[0] or rates[1])):
                raise TaxBindingUnavailable("Invalid SGST/CGST/IGST combination")


def select_zone_gst(bindings: list[ZoneGSTBinding], zone_id: str, channel: str, at: datetime) -> ZoneGSTBinding:
    """Exactly one effective approved binding; no silent fallback to zero GST."""
    instant = _aware(at)
    if not isinstance(channel, str) or channel not in _MODES:
        raise TaxBindingUnavailable("Valid tax service channel required")
    matched = [b for b in bindings if b.zone_id == zone_id and b.channel == channel and
               (b.starts_at is None or _aware(b.starts_at) <= instant) and
               (b.ends_at is None or instant < _aware(b.ends_at))]
    if len(matched) != 1:
        raise TaxBindingUnavailable("Missing or ambiguous effective zone GST binding")
    return matched[0]


def _validate_profile(profile: Any) -> Mapping[str, Any]:
    _exact_keys(profile, {"charging", "transport", "distance", "extras"}, {"charging", "transport", "distance", "extras"}, "tariff profile")
    charging = _exact_keys(profile["charging"], {"basis", "rate", "tax_class", "minute_rounding"}, {"basis", "rate", "tax_class"}, "primary charging")
    if not isinstance(charging["basis"], str) or charging["basis"] not in _PRIMARY:
        raise CommercialContractError("Exactly one primary charging basis required")
    _money(charging["rate"], "charging rate")
    _id(charging["tax_class"], "charging tax class")
    if charging["basis"] == "TIME_MINUTE" and charging.get("minute_rounding") not in _ROUNDING:
        raise CommercialContractError("Time-billed charging requires explicit minute rounding")
    if charging["basis"] != "TIME_MINUTE" and "minute_rounding" in charging:
        raise CommercialContractError("Minute rounding only applies to time billing")
    transport = _exact_keys(profile["transport"], {"rate", "tax_class", "applies_on"}, {"rate", "tax_class", "applies_on"}, "transport")
    _money(transport["rate"], "transport rate")
    _id(transport["tax_class"], "transport tax class")
    _events(transport["applies_on"], "transport")
    distance = _exact_keys(profile["distance"], {"minimum_billable_m", "slabs", "tax_class", "applies_on"}, {"minimum_billable_m", "slabs", "tax_class", "applies_on"}, "distance")
    _int(distance["minimum_billable_m"], "minimum_billable_m")
    _id(distance["tax_class"], "distance tax class")
    _events(distance["applies_on"], "distance")
    slabs = distance["slabs"]
    if not isinstance(slabs, list) or not 1 <= len(slabs) <= 32:
        raise CommercialContractError("Distance requires 1..32 progressive slabs")
    previous = 0
    for idx, slab in enumerate(slabs):
        _exact_keys(slab, {"up_to_m", "rate_per_km"}, {"up_to_m", "rate_per_km"}, "slab")
        _money(slab["rate_per_km"], "distance rate")
        upper = slab["up_to_m"]
        if upper is None:
            if idx != len(slabs) - 1:
                raise CommercialContractError("Only final distance slab may be unbounded")
        else:
            _int(upper, "slab boundary")
            if upper <= previous:
                raise CommercialContractError("Distance slab boundaries must strictly increase")
            previous = upper
    if slabs[-1]["up_to_m"] is not None:
        raise CommercialContractError("Final distance slab must be unbounded")
    extras = profile["extras"]
    if not isinstance(extras, list) or len(extras) > 48:
        raise CommercialContractError("Too many optional fees")
    ids = set()
    for extra in extras:
        _exact_keys(extra, {"id", "basis", "rate", "tax_class", "applies_on", "minute_rounding"}, {"id", "basis", "rate", "tax_class", "applies_on"}, "extra fee")
        name = _id(extra["id"], "extra ID")
        if name in ids or name in {"TRANSPORT", "CHARGING"} or name.startswith("DIST_"):
            raise CommercialContractError("Duplicate/reserved extra ID")
        ids.add(name)
        if not isinstance(extra["basis"], str) or extra["basis"] not in {"FIXED", "WAITING_MINUTE"}:
            raise CommercialContractError("Unsupported extra charge basis")
        _money(extra["rate"], "extra rate")
        _id(extra["tax_class"], "extra tax class")
        _events(extra["applies_on"], "extra fee")
        if extra["basis"] == "WAITING_MINUTE" and "UPFRONT" in extra["applies_on"]:
            raise CommercialContractError("Waiting cannot be billed before service")
        if extra["basis"] == "WAITING_MINUTE" and extra.get("minute_rounding") not in _ROUNDING:
            raise CommercialContractError("Waiting fees require explicit minute rounding")
        if extra["basis"] != "WAITING_MINUTE" and "minute_rounding" in extra:
            raise CommercialContractError("Minute rounding only applies to waiting fees")
    return profile


def validate_tariff_plan(plan: Mapping[str, Any]) -> None:
    _exact_keys(plan, {"policy_id", "policy_version", "zone_id", "channel", "currency", "profiles"}, {"policy_id", "policy_version", "zone_id", "channel", "currency", "profiles"}, "zone tariff plan")
    _id(plan["policy_id"], "policy ID")
    if type(plan["policy_version"]) is not int or not 1 <= plan["policy_version"] <= _MAX_QUANTITY:
        raise CommercialContractError("Invalid policy version")
    _key(plan["zone_id"], "zone_id")
    if not isinstance(plan["channel"], str) or plan["channel"] not in _MODES or plan["currency"] != "INR":
        raise CommercialContractError("Unsupported channel/currency")
    profiles = plan["profiles"]
    if not isinstance(profiles, dict) or "INSIDE" not in profiles or set(profiles) - _RELATIONS:
        raise CommercialContractError("Tariff requires INSIDE profile; OUTSIDE is optional")
    for p in profiles.values():
        _validate_profile(p)
        if plan["channel"] == "ON_SITE_QR" and (
            Decimal(p["transport"]["rate"]) != 0
            or any(Decimal(s["rate_per_km"]) != 0 for s in p["distance"]["slabs"])
        ):
            raise CommercialContractError("On-site QR plan must not invent transport/distance charges")


def _trusted_measure(evidence: Evidence, name: str, phase: str) -> tuple[int, str, bool]:
    value = getattr(evidence, name)
    if value is None:
        raise CommercialContractError(f"Authoritative measurement missing: {name}")
    src = _trusted_source(evidence.sources or {}, name)
    estimated = name in evidence.estimated
    if phase != "UPFRONT" and estimated:
        # Completed/cancelled financial finality must never use an estimate.
        raise CommercialContractError("Final charge cannot use estimated measurements")
    return value, src, estimated


def compile_tariff_evaluation(
    plan: Mapping[str, Any], context: TrustedCommercialContext,
    evidence: Evidence, phase: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile one profile into existing Go-parity BASE rule engine inputs.

    No actual money is calculated here. Sources and quantities are explicit.
    Progressive slabs are disjoint quantities; each has a stable rate/rule ID.
    """
    validate_tariff_plan(plan)
    if not isinstance(phase, str) or phase not in _PHASES or plan["zone_id"] != context.zone_id or plan["channel"] != context.channel:
        raise CommercialContractError("Policy/context/phase mismatch")
    profile = plan["profiles"].get(context.relation)
    if profile is None:
        raise CommercialContractError("No tariff profile for location relation")
    rules: list[dict[str, Any]] = []
    measures: dict[str, dict[str, Any]] = {}

    def add(id: str, component: str, basis: str, rate: str, tax: str, quantity: str,
            source: str, estimated: bool = False) -> None:
        _id(id, "compiled rule")
        if not _RATE.fullmatch(quantity):
            raise CommercialContractError("Compiled quantity exceeds allowed precision")
        rules.append({"id": id, "kind": "BASE", "priority": len(rules), "component": component,
                      "basis": basis, "rate": rate, "tax_class": tax})
        measures[basis] = {"quantity": quantity, "source": source, "estimate": estimated}

    transport = profile["transport"]
    if phase in transport["applies_on"]:
        add("TRANSPORT", "TRANSPORT", "TRANSPORT_COUNT", transport["rate"], transport["tax_class"], "1", "FROZEN_TARIFF")

    distance = profile["distance"]
    if phase in distance["applies_on"]:
        if phase == "UPFRONT":
            # Contractually precollects configured minimum, not estimated driving.
            meters, source, estimated = distance["minimum_billable_m"], "FROZEN_DISTANCE_MINIMUM", False
        else:
            measured, source, estimated = _trusted_measure(evidence, "travel_m", phase)
            meters = max(measured, distance["minimum_billable_m"])
        lower = 0
        for index, slab in enumerate(distance["slabs"], 1):
            upper = slab["up_to_m"]
            slab_m = max(0, min(meters, upper) - lower) if upper is not None else max(0, meters - lower)
            add(f"DIST_{index}", f"DIST_{index}", f"DISTANCE_KM_{index}", slab["rate_per_km"],
                distance["tax_class"], _quantity(slab_m, 1000), source, estimated)
            if upper is None:
                break
            lower = upper

    if phase == "COMPLETED":
        charging = profile["charging"]
        kind = charging["basis"]
        if kind == "SESSION":
            add("CHARGING", "CHARGING", "SESSION_COUNT", charging["rate"], charging["tax_class"], "1", "HAL_SESSION_COMPLETED")
        elif kind == "ENERGY_KWH":
            energy, src, est = _trusted_measure(evidence, "energy_wh", phase)
            add("CHARGING", "CHARGING", "CHARGED_KWH", charging["rate"], charging["tax_class"], _quantity(energy, 1000), src, est)
        else:
            seconds, src, est = _trusted_measure(evidence, "charging_seconds", phase)
            add("CHARGING", "CHARGING", "CHARGED_MINUTES", charging["rate"], charging["tax_class"],
                _minute_quantity(seconds, charging["minute_rounding"]), src, est)

    for extra in profile["extras"]:
        if phase not in extra["applies_on"]:
            continue
        if extra["basis"] == "FIXED":
            add(extra["id"], extra["id"], f"EXTRA_{extra['id']}", extra["rate"], extra["tax_class"], "1", "FROZEN_TARIFF_EVENT")
        else:
            secs, src, est = _trusted_measure(evidence, "waiting_seconds", phase)
            add(extra["id"], extra["id"], f"EXTRA_{extra['id']}", extra["rate"], extra["tax_class"],
                _minute_quantity(secs, extra["minute_rounding"]), src, est)

    if not rules:
        raise CommercialContractError("No applicable components in this outcome")
    terms = {"policy_id": plan["policy_id"], "policy_version": plan["policy_version"],
             "currency": "INR", "rules": rules}
    return terms, {"measurements": measures, "conditions": {}}


def evaluate_zone_tariff(
    plan: Mapping[str, Any], context: TrustedCommercialContext, evidence: Evidence,
    phase: str, gst: ZoneGSTBinding, *, engine=None,
) -> dict[str, Any]:
    """Evaluate via the EXISTING pricing_engine; never authorize a wallet debit.

    `engine` is for deterministic pure unit testing/injection only. In application
    operation, the default is cow.services.pricing_engine.evaluate.
    """
    if gst.zone_id != context.zone_id or gst.channel != context.channel:
        raise TaxBindingUnavailable("Zone GST binding does not match zone and channel")
    if gst is not select_zone_gst([gst], context.zone_id, context.channel, context.decision_at):
        raise TaxBindingUnavailable("Zone GST binding not applicable to commercial context")
    terms, inputs = compile_tariff_evaluation(plan, context, evidence, phase)
    if engine is None:
        from cow.services.pricing_engine import evaluate as real_evaluate
        selected_engine = real_evaluate
    else:
        selected_engine = engine
    # An immutable semantic snapshot of decisions, not payment authorization.
    result = selected_engine(terms, inputs, copy.deepcopy(dict(gst.tax_profiles)), "FINAL" if phase != "UPFRONT" else "PREVIEW")
    if not isinstance(result, dict) or type(result.get("total_minor")) is not int or result["total_minor"] < 0 or result["total_minor"] > _MAX_MINOR:
        raise CommercialContractError("Invalid pricing engine result")
    return {**result,
            "commercial_context": {"zone_id": context.zone_id, "channel": context.channel,
                                   "relation": context.relation, "decision_at": _aware(context.decision_at).isoformat(),
                                   "provenance": context.provenance,
                                   "driver_duty_id": context.driver_duty_id, "site_id": context.site_id},
            "tax_binding": {"channel": gst.channel, "version": gst.binding_version, "seller_tax_ref": gst.seller_tax_ref,
                             "legal_decision_ref": gst.legal_decision_ref,
                             "tax_profiles": copy.deepcopy(dict(gst.tax_profiles))},
            "phase": phase,
            "policy_snapshot": copy.deepcopy(dict(plan))}


@dataclass(frozen=True)
class CollectionInstruction:
    """Pure proposal, NOT a posted ledger entry or evidence of wallet balance."""
    job_id: str
    upfront_debit_minor: int
    already_posted_upfront_minor: int
    final_total_minor: int
    additional_debit_minor: int
    customer_credit_minor: int
    upfront_key: str
    final_key: str
    credit_key: str


def plan_two_stage_collection(job_id: str, upfront_due_minor: int, posted_upfront_minor: int,
                              final_total_minor: int, *, posted_upfront_receipt: str | None) -> CollectionInstruction:
    """Preserves one upfront collection and at most one net final debit/credit.

    A caller must confirm *actual* posted upfront ledger evidence, and persist
    final collection/refund with its stable idempotency key transactionally.
    """
    try:
        canonical = str(UUID(job_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CommercialContractError("Canonical job UUID required") from exc
    for label, n in (("upfront_due", upfront_due_minor), ("posted_upfront", posted_upfront_minor), ("final_total", final_total_minor)):
        _int(n, label, _MAX_MINOR)
    if posted_upfront_minor not in (0, upfront_due_minor):
        raise CommercialContractError("Partial/unknown upfront settlement requires reconciliation")
    if posted_upfront_minor and (not isinstance(posted_upfront_receipt, str) or not posted_upfront_receipt.strip()):
        raise CommercialContractError("Posted upfront debit requires durable ledger receipt")
    if posted_upfront_minor == 0 and posted_upfront_receipt is not None:
        raise CommercialContractError("Unposted debit cannot have posted receipt")
    if posted_upfront_minor != upfront_due_minor:
        raise CommercialContractError("Cannot finalize before upfront collection is evidenced")
    additional = max(0, final_total_minor - posted_upfront_minor)
    credit = max(0, posted_upfront_minor - final_total_minor)
    return CollectionInstruction(canonical, upfront_due_minor, posted_upfront_minor, final_total_minor,
                                 additional, credit, f"cow:upfront:{canonical}",
                                 f"cow:final:{canonical}", f"cow:credit:{canonical}")


@dataclass(frozen=True)
class PublishedZoneTariff:
    """Versioned repository projection; persistence/version lifecycle is caller-owned."""
    plan: Mapping[str, Any]
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    published: bool = True

    def __post_init__(self) -> None:
        if type(self.published) is not bool:
            raise CommercialContractError("Publication state must be boolean")


def select_published_zone_plan(
    catalog: list[PublishedZoneTariff], context: TrustedCommercialContext,
    *, resolver=None,
) -> dict[str, Any]:
    """Use existing pricing_topology, NOT a parallel temporal selector.

    This expects a trusted repository catalog. The resulting plan must be
    snapshotted with the accepted quote; do not reselect it during settlement.
    """
    if not isinstance(catalog, list) or not catalog:
        raise CommercialContractError("No trusted published zone tariffs")
    if resolver is None:
        from cow.services.pricing_topology import resolve as topology_resolve
        resolver = topology_resolve
    # Mode is part of the exact scope: roaming and QR timelines must not collide.
    scope = {"kind": "ZONE_" + context.channel, "key": context.zone_id}
    projections = []
    candidates = {}
    for entry in catalog:
        if not isinstance(entry, PublishedZoneTariff):
            raise CommercialContractError("Invalid tariff catalog entry")
        validate_tariff_plan(entry.plan)
        if entry.plan["zone_id"] != context.zone_id or entry.plan["channel"] != context.channel:
            continue
        if entry.starts_at is not None:
            _aware(entry.starts_at)
        if entry.ends_at is not None:
            _aware(entry.ends_at)
        if entry.ends_at is not None and (entry.starts_at is None or _aware(entry.ends_at) <= _aware(entry.starts_at)):
            raise CommercialContractError("Invalid published window")
        identity = (entry.plan["policy_id"], entry.plan["policy_version"])
        if identity in candidates:
            raise CommercialContractError("Repeated tariff identity")
        candidates[identity] = entry.plan
        # Valid, deliberately inert terms let the canonical topology engine
        # check identity/window/scope. They do NOT enter financial evaluation.
        terms = {"policy_id": identity[0], "policy_version": identity[1],
                 "currency": "INR", "rules": [{"id": "ROOT_SENTINEL", "kind": "BASE",
                 "component": "ROOT_SENTINEL", "basis": "SENTINEL", "rate": "0", "tax_class": "SENTINEL"}]}
        projections.append({"scope": scope, "starts_at": entry.starts_at,
                            "ends_at": entry.ends_at, "active": entry.published, "terms": terms})
    if not projections or not any(p["active"] for p in projections):
        raise CommercialContractError("No published plan for zone and channel")
    winner = resolver(projections, [scope], _aware(context.decision_at))
    selected = winner["terms"]
    identity = (selected["policy_id"], selected["policy_version"])
    if identity not in candidates:
        raise CommercialContractError("Topology resolved unknown plan")
    return copy.deepcopy(dict(candidates[identity]))
