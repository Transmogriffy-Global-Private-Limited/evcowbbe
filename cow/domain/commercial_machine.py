"""EVCOWBBE unified fare machine v1 (pure, deterministic, no ORM or side effects).

This is a *calculation/resolution* boundary. It does not authorize service,
certify tax law, accept quotes, charge wallets, control HAL or issue tax invoices.
The caller MUST supply independently verified business facts and approved,
immutable configuration snapshots. Refuse malformed/ambiguous inputs.

Amounts in the output are integer INR paise; rates are exact decimal strings.
Measurements are integer Wh, metres and seconds. No float arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from re import fullmatch
from typing import Any, Mapping, Sequence
import copy

CHANNELS = frozenset({'ROAMING', 'ON_SITE_QR'})
EVENTS = frozenset({'UPFRONT', 'COMPLETED', 'CANCELLED', 'NO_SHOW'})
MODES = frozenset({'INCLUSIVE', 'EXCLUSIVE'})
TREATMENTS = frozenset({'TAXABLE', 'EXEMPT', 'NIL_RATED', 'ZERO_RATED', 'OUTSIDE_SCOPE'})
HEADS = ('CGST', 'SGST', 'UTGST', 'IGST')
MAX_MINOR = 2**63 - 1
MAX_SLABS = 32
MAX_EXTRAS = 48
ENGINE_VERSION = 'cow-commercial-v1'


class CommercialError(ValueError):
    """Machine error includes a stable diagnostic, not a fabricated quote."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def fail(code: str, message: str):
    raise CommercialError(code, message)


def exact(raw: Any, name: str, *, max_value: str = '999999999999.999999') -> Decimal:
    if not isinstance(raw, str) or not fullmatch(r'(?:0|[1-9]\d{0,11})(?:\.\d{1,6})?', raw):
        fail('INVALID_DECIMAL', f'{name}: expected exact unsigned decimal string, at most 6 places')
    try:
        value = Decimal(raw)
    except InvalidOperation:
        fail('INVALID_DECIMAL', f'{name}: invalid number')
    if value > Decimal(max_value):
        fail('AMOUNT_LIMIT', f'{name}: exceeds supported bound')
    return value


def valid_key(value: Any, what: str) -> str:
    if not isinstance(value, str) or not fullmatch(r'[A-Z][A-Z0-9_]{0,63}', value):
        fail('INVALID_IDENTIFIER', f'{what}: invalid identifier')
    return value


def aware(value: datetime, what: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        fail('INVALID_TIME', f'{what}: timezone-aware datetime required')
    return value


def window(start: datetime | None, end: datetime | None):
    if start is not None:
        aware(start, 'start')
    if end is not None:
        aware(end, 'end')
    if end is not None and start is not None and start >= end:
        fail('INVALID_WINDOW', 'end must be later than start')


def applies(start: datetime | None, end: datetime | None, at: datetime) -> bool:
    return (start is None or start <= at) and (end is None or at < end)


def check_keys(value: Any, required: set[str], allowed: set[str], where: str) -> dict:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - allowed:
        fail('INVALID_SHAPE', f'{where}: required keys missing or unknown keys present')
    return value


def integer(raw: Any, name: str, lo: int = 0, hi: int = 2**63 - 1) -> int:
    if type(raw) is not int or not lo <= raw <= hi:
        fail('INVALID_QUANTITY', f'{name}: expected integer in permitted range')
    return raw


def validate_tariff(plan: Mapping[str, Any]) -> None:
    """Validate a reusable, target-independent tariff and its bounded blocks."""
    check_keys(plan, {'key', 'version', 'channel', 'currency', 'price_mode', 'profiles'},
               {'key', 'version', 'channel', 'currency', 'price_mode', 'profiles'}, 'tariff')
    valid_key(plan['key'], 'tariff key')
    integer(plan['version'], 'tariff version', 1, 2**31-1)
    if (not isinstance(plan['channel'],str) or plan['channel'] not in CHANNELS or
        plan['currency'] != 'INR' or not isinstance(plan['price_mode'],str) or plan['price_mode'] not in MODES):
        fail('INVALID_TARIFF', 'Unsupported channel, currency or price mode')
    profiles = plan['profiles']
    if not isinstance(profiles, dict) or not profiles or 'INSIDE' not in profiles or set(profiles) - {'INSIDE', 'OUTSIDE'}:
        fail('INVALID_TARIFF', 'INSIDE profile required; OUTSIDE optional')
    for relation, components in profiles.items():
        check_keys(components, {'charging'}, {'charging', 'visit', 'distance', 'extras'}, 'tariff profile')
        charging = check_keys(components['charging'], {'basis', 'rate', 'tax_class'},
                              {'basis', 'rate', 'tax_class', 'price_mode', 'minute_rounding'}, 'charging')
        if not isinstance(charging['basis'],str) or charging['basis'] not in {'ENERGY_KWH', 'TIME_MINUTE', 'SESSION'}:
            fail('INVALID_TARIFF', 'Exactly one supported charging basis required')
        exact(charging['rate'], 'charging rate')
        valid_key(charging['tax_class'], 'charging tax class')
        _price_mode(charging.get('price_mode'), plan['price_mode'])
        if charging['basis'] == 'TIME_MINUTE':
            _rounding(charging.get('minute_rounding'))
        elif charging.get('minute_rounding') is not None:
            fail('INVALID_TARIFF', 'Minute rounding only belongs to time charging')
        for component in ('visit', 'distance'):
            if component not in components:
                continue
            item = components[component]
            check_keys(item, {'tax_class', 'applies_on'},
                       ({'tax_class', 'applies_on', 'rate', 'price_mode'} if component == 'visit' else
                        {'tax_class', 'applies_on', 'minimum_billable_m', 'slabs', 'price_mode'}), component)
            valid_key(item['tax_class'], f'{component} tax class')
            _events(item['applies_on'])
            _price_mode(item.get('price_mode'), plan['price_mode'])
            if component == 'visit':
                if 'rate' not in item:
                    fail('INVALID_TARIFF', 'Visit rate missing')
                exact(item['rate'], 'visit rate')
            else:
                integer(item.get('minimum_billable_m'), 'minimum_billable_m')
                slabs = item.get('slabs')
                if not isinstance(slabs, list) or not 1 <= len(slabs) <= MAX_SLABS:
                    fail('INVALID_TARIFF', '1-32 distance slabs required')
                previous = 0
                for i, slab in enumerate(slabs):
                    check_keys(slab, {'up_to_m', 'rate_per_km'}, {'up_to_m', 'rate_per_km'}, 'distance slab')
                    exact(slab['rate_per_km'], 'distance rate')
                    bound = slab['up_to_m']
                    if bound is None:
                        if i != len(slabs)-1:
                            fail('INVALID_TARIFF', 'Unbounded distance slab must be last')
                    else:
                        integer(bound, 'distance upper bound', previous+1)
                        if i == len(slabs)-1:
                            fail('INVALID_TARIFF', 'Final slab must be unbounded')
                        previous = bound
        extras = components.get('extras', [])
        if not isinstance(extras, list) or len(extras) > MAX_EXTRAS:
            fail('INVALID_TARIFF', 'Extras must be a bounded list')
        seen = set()
        for extra in extras:
            check_keys(extra, {'id', 'basis', 'rate', 'tax_class', 'applies_on'},
                       {'id', 'basis', 'rate', 'tax_class', 'applies_on', 'price_mode', 'minute_rounding', 'grace_seconds'}, 'extra')
            name = valid_key(extra['id'], 'extra id')
            if name in seen or name in {'CHARGING', 'VISIT', 'DISTANCE'}:
                fail('INVALID_TARIFF', 'Duplicate or reserved extra identity')
            seen.add(name)
            if not isinstance(extra['basis'],str) or extra['basis'] not in {'FIXED', 'WAITING_MINUTE'}:
                fail('INVALID_TARIFF', 'Unsupported extra basis')
            exact(extra['rate'], 'extra rate')
            valid_key(extra['tax_class'], 'extra tax class')
            _events(extra['applies_on'])
            _price_mode(extra.get('price_mode'), plan['price_mode'])
            if extra['basis'] == 'WAITING_MINUTE':
                _rounding(extra.get('minute_rounding'))
                integer(extra.get('grace_seconds', 0), 'waiting grace', 0, 86400)
            elif extra.get('minute_rounding') is not None or extra.get('grace_seconds') is not None:
                fail('INVALID_TARIFF', 'Fixed extras cannot have waiting parameters')
        if plan['channel'] == 'ON_SITE_QR' and ('visit' in components or 'distance' in components):
            fail('INVALID_CHANNEL_BLOCK', 'On-site QR cannot use roaming visit or travel fees')


def _price_mode(value: Any, default: str) -> str:
    chosen = default if value is None else value
    if not isinstance(chosen,str) or chosen not in MODES:
        fail('INVALID_PRICE_MODE', 'Only INCLUSIVE or EXCLUSIVE is supported')
    return chosen


def _rounding(value: Any):
    if value not in {'CEIL_STARTED_MINUTE', 'FLOOR_COMPLETED_MINUTE'}:
        fail('INVALID_ROUNDING', 'Explicit supported minute-rounding policy required')


def _events(raw: Any):
    if not isinstance(raw,list) or not raw or any(not isinstance(x,str) or x not in EVENTS for x in raw) or len(set(raw)) != len(raw):
        fail('INVALID_EVENT_RULE', 'At least one unique supported event required')


@dataclass(frozen=True)
class Assignment:
    id: str
    tariff_key: str
    tariff_version: int
    target_type: str  # ZONE or GROUP
    target_id: str
    channel: str
    slot: str  # DEFAULT or SCHEDULED
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.target_type,str) or self.target_type not in {'ZONE', 'GROUP'} or not isinstance(self.channel,str) or self.channel not in CHANNELS:
            fail('INVALID_ASSIGNMENT', 'Invalid tariff assignment target or channel')
        if not self.id or not self.target_id or self.slot not in {'DEFAULT', 'SCHEDULED'}:
            fail('INVALID_ASSIGNMENT', 'Invalid assignment identity or slot')
        valid_key(self.tariff_key, 'assigned tariff key')
        integer(self.tariff_version, 'assigned tariff version', 1)
        window(self.starts_at, self.ends_at)
        if self.slot == 'SCHEDULED' and (self.starts_at is None or self.ends_at is None):
            fail('INVALID_ASSIGNMENT', 'SCHEDULED requires bounded interval')


@dataclass(frozen=True)
class GSTProfile:
    id: str
    version: int
    seller_ref: str
    legal_decision_ref: str
    status: str
    tax_classes: Mapping[str, Mapping[str, Any]]

    def __post_init__(self):
        if not self.id or not self.seller_ref or not self.legal_decision_ref:
            fail('INVALID_GST', 'GST profile identity, seller and legal reference required')
        integer(self.version, 'GST profile version', 1)
        if not isinstance(self.status,str) or self.status not in {'DRAFT', 'APPROVED', 'RETIRED', 'REVOKED'}:
            fail('INVALID_GST', 'Invalid GST state')
        if not isinstance(self.tax_classes, dict) or not self.tax_classes:
            fail('INVALID_GST', 'GST tax-class catalog required')
        for name, item in self.tax_classes.items():
            valid_key(name, 'GST tax class')
            check_keys(item, {'treatment', 'supply_group', 'rates'},
                       {'treatment', 'supply_group', 'rates'}, 'tax class')
            if not isinstance(item['treatment'],str) or item['treatment'] not in TREATMENTS:
                fail('INVALID_GST', 'Unsupported legal tax treatment')
            valid_key(item['supply_group'], 'supply grouping')
            rates = check_keys(item['rates'], set(HEADS), set(HEADS), 'GST rates')
            rr = {h: exact(rates[h], h, max_value='100') for h in HEADS}
            if rr['IGST'] and (rr['CGST'] or rr['SGST'] or rr['UTGST']):
                fail('GST_HEAD_CONFLICT', 'IGST cannot combine with CGST/SGST/UTGST')
            if rr['SGST'] and rr['UTGST']:
                fail('GST_HEAD_CONFLICT', 'SGST and UTGST cannot combine')
            present={h for h,v in rr.items() if v>0}
            if present not in (set(), {'CGST','SGST'}, {'CGST','UTGST'}, {'IGST'}):
                fail('GST_HEAD_CONFLICT', 'GST heads must be CGST+SGST, CGST+UTGST, IGST, or approved zero')
            if present=={'CGST','SGST'} and rr['CGST']!=rr['SGST']:
                fail('GST_HEAD_CONFLICT', 'CGST and SGST percentages must match')
            if present=={'CGST','UTGST'} and rr['CGST']!=rr['UTGST']:
                fail('GST_HEAD_CONFLICT', 'CGST and UTGST percentages must match')
            if sum(rr.values(), Decimal(0))>100:
                fail('GST_HEAD_CONFLICT', 'Combined GST percent exceeds engine bound')
            if item['treatment'] != 'TAXABLE' and any(rr.values()):
                fail('INVALID_GST', 'Non-taxable treatment must have zero output rates')
        groups = {}
        for name, item in self.tax_classes.items():
            k = item['supply_group']
            terms = (item['treatment'], tuple(item['rates'][h] for h in HEADS))
            if k in groups and groups[k] != terms:
                fail('GST_SUPPLY_CONFLICT', 'A supply group cannot have contradictory tax treatment')
            groups[k] = terms


@dataclass(frozen=True)
class GSTAssignment:
    id: str
    zone_id: str
    channel: str
    profile_id: str
    profile_version: int
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    def __post_init__(self):
        if not all((self.id, self.zone_id, self.profile_id)) or not isinstance(self.channel,str) or self.channel not in CHANNELS:
            fail('INVALID_GST_ASSIGNMENT', 'Incomplete GST assignment')
        integer(self.profile_version, 'GST assignment version', 1)
        window(self.starts_at, self.ends_at)


@dataclass(frozen=True)
class Context:
    zone_id: str
    channel: str
    relation: str
    at: datetime
    seller_ref: str
    pricing_group_id: str | None = None
    serviceable: bool = True
    operational: bool = True
    verified: bool = False

    def __post_init__(self):
        aware(self.at, 'decision time')
        if not self.zone_id or not self.seller_ref or not isinstance(self.channel,str) or self.channel not in CHANNELS or not isinstance(self.relation,str) or self.relation not in {'INSIDE', 'OUTSIDE'}:
            fail('INVALID_CONTEXT', 'Missing or invalid server-owned context')
        if type(self.serviceable) is not bool or type(self.operational) is not bool or type(self.verified) is not bool:
            fail('INVALID_CONTEXT', 'Trust/status flags must be booleans')


@dataclass(frozen=True)
class Measurement:
    value: int
    source: str
    estimated: bool = False

    def __post_init__(self):
        integer(self.value, 'measurement')
        if not isinstance(self.source, str) or not self.source.strip() or type(self.estimated) is not bool:
            fail('INVALID_EVIDENCE', 'Measurement needs trusted source metadata')


def validate_assignments(assignments: Sequence[Assignment], tariffs: Mapping[tuple, Mapping[str, Any]],
                         *, operational_zones: Sequence[tuple[str, str]] = ()) -> None:
    """Validate active catalog topology. This check must also be DB-enforced on mutations."""
    groups: dict[tuple, list[Assignment]] = {}
    ids = set()
    for a in assignments:
        if a.id in ids:
            fail('ASSIGNMENT_CONFLICT', 'Duplicate assignment ID')
        ids.add(a.id)
        plan = tariffs.get((a.tariff_key, a.tariff_version))
        if plan is None:
            fail('ASSIGNMENT_TARIFF_MISSING', 'Assignment references absent tariff')
        validate_tariff(plan)
        if plan['channel'] != a.channel:
            fail('ASSIGNMENT_CHANNEL_MISMATCH', 'Tariff channel incompatible with target')
        groups.setdefault((a.target_type, a.target_id, a.channel), []).append(a)
    for key, rows in groups.items():
        roots = [x for x in rows if x.slot == 'DEFAULT']
        scheduled = [x for x in rows if x.slot == 'SCHEDULED']
        for collection in (roots, scheduled):
            for i, first in enumerate(collection):
                for second in collection[i+1:]:
                    # Unbounded start/end are negative/positive infinity.
                    if ((first.ends_at is None or second.starts_at is None or first.ends_at > second.starts_at) and
                        (second.ends_at is None or first.starts_at is None or second.ends_at > first.starts_at)):
                        fail('ASSIGNMENT_CONFLICT', 'Overlapping assignment windows for same slot')
        for override in scheduled:
            if not any((root.starts_at is None or root.starts_at <= override.starts_at) and
                       (root.ends_at is None or root.ends_at >= override.ends_at) for root in roots):
                fail('ASSIGNMENT_CONFLICT', 'Scheduled tariff must have uninterrupted default coverage')
    for zone, channel in operational_zones:
        if not any(x.slot == 'DEFAULT' for x in groups.get(('ZONE', zone, channel), [])):
            fail('ZONE_DEFAULT_REQUIRED', 'Operational Zone/channel needs a default tariff')


def select_tariff(context: Context, assignments: Sequence[Assignment], tariffs: Mapping[tuple, Mapping[str, Any]]) -> tuple[Mapping[str, Any], Assignment]:
    if not context.serviceable or not context.operational:
        fail('SERVICE_UNAVAILABLE', 'Zone or channel not serviceable and operational')
    validate_assignments(assignments, tariffs, operational_zones=[(context.zone_id, context.channel)])
    zone_roots = [a for a in assignments if a.target_type == 'ZONE' and
                  a.target_id == context.zone_id and a.channel == context.channel and
                  a.slot == 'DEFAULT' and applies(a.starts_at, a.ends_at, context.at)]
    if len(zone_roots) != 1:
        fail('ZONE_DEFAULT_REQUIRED', 'Operational Zone needs one effective default, even for Group-priced customers')
    targets = ([('GROUP', context.pricing_group_id)] if context.pricing_group_id else []) + [('ZONE', context.zone_id)]
    for target_type, target_id in targets:
        matches = [x for x in assignments if x.target_type == target_type and x.target_id == target_id and x.channel == context.channel]
        scheduled = [x for x in matches if x.slot == 'SCHEDULED' and applies(x.starts_at, x.ends_at, context.at)]
        roots = [x for x in matches if x.slot == 'DEFAULT' and applies(x.starts_at, x.ends_at, context.at)]
        if len(scheduled) > 1 or len(roots) > 1:
            fail('ASSIGNMENT_CONFLICT', 'Ambiguous effective tariff selection')
        picked = (scheduled or roots)
        if picked:
            obj = picked[0]
            plan = tariffs[(obj.tariff_key, obj.tariff_version)]
            if context.relation not in plan['profiles']:
                fail('TARIFF_RELATION_UNAVAILABLE', 'Selected tariff has no authorized relation profile')
            return plan, obj
    fail('ZONE_DEFAULT_REQUIRED', 'No effective tariff')


def select_gst(context: Context, rows: Sequence[GSTAssignment], profiles: Mapping[tuple, GSTProfile]) -> tuple[GSTProfile, GSTAssignment]:
    matches = [a for a in rows if a.zone_id == context.zone_id and a.channel == context.channel and applies(a.starts_at, a.ends_at, context.at)]
    if len(matches) != 1:
        fail('GST_COVERAGE_INVALID', 'Exactly one effective GST assignment required')
    assignment = matches[0]
    profile = profiles.get((assignment.profile_id, assignment.profile_version))
    if profile is None or profile.status != 'APPROVED':
        fail('GST_NOT_APPROVED', 'Referenced GST version missing or unapproved')
    if profile.seller_ref != context.seller_ref:
        fail('GST_SELLER_MISMATCH', 'GST seller scope mismatches trusted context')
    return profile, assignment


def minor(amount_rupees: Decimal) -> int:
    v = int((amount_rupees * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    if v < 0 or v > MAX_MINOR:
        fail('MONEY_OVERFLOW', 'Monetary amount outside signed 64-bit paise range')
    return v


def split_tax(gross_or_base_minor: int, rates: Mapping[str, Decimal], mode: str) -> tuple[int, dict[str,int], int]:
    """One tax total rounded once, then deterministic largest-remainder heads."""
    integer(gross_or_base_minor, 'price minor')
    total_rate = sum(rates.values(), Decimal(0))
    if total_rate == 0:
        return gross_or_base_minor, {h: 0 for h in HEADS}, gross_or_base_minor
    base = Decimal(gross_or_base_minor)
    if mode == 'INCLUSIVE':
        tax_total = int((base * total_rate / (Decimal(100) + total_rate)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        net = gross_or_base_minor-tax_total
        gross = gross_or_base_minor
    else:
        net = gross_or_base_minor
        tax_total = int((base * total_rate / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        gross = net+tax_total
        if gross > MAX_MINOR:
            fail('MONEY_OVERFLOW', 'Gross monetary amount overflow')
    if tax_total > gross:
        fail('TAX_INVALID', 'Tax exceeds gross amount')
    fractions = {h: Decimal(tax_total) * rates[h] / total_rate for h in HEADS}
    amounts = {h: int(fractions[h]) for h in HEADS}
    remaining = tax_total-sum(amounts.values())
    ranking = sorted(HEADS, key=lambda h: (-(fractions[h]-amounts[h]), HEADS.index(h)))
    for h in ranking[:remaining]:
        amounts[h] += 1
    if sum(amounts.values()) != tax_total or net+sum(amounts.values()) != gross:
        fail('TAX_RECONCILIATION_FAILED', 'Tax reconciliation invariant violated')
    return net, amounts, gross


def _billable_minutes(seconds: int, rounding: str) -> int:
    if rounding == 'CEIL_STARTED_MINUTE':
        return (seconds+59)//60
    if rounding == 'FLOOR_COMPLETED_MINUTE':
        return seconds//60
    fail('INVALID_ROUNDING', 'Unsupported minute rounding')


def evaluate(*, context: Context, event: str, assignments: Sequence[Assignment],
             tariffs: Mapping[tuple, Mapping[str, Any]], gst_assignments: Sequence[GSTAssignment],
             gst_profiles: Mapping[tuple, GSTProfile], measurements: Mapping[str, Measurement],
             mode: str = 'PREVIEW') -> dict[str, Any]:
    """Return complete breakdown from approved snapshots. PREVIEW is never binding.

    No DB lookup, wallet, location provider, GST provider, command or mutation.
    In a real HTTP flow, context.verified MUST be server-derived and not a flag
    accepted from the customer. FINAL requires verified trusted final evidence.
    """
    if event not in EVENTS or mode not in {'PREVIEW','FINAL'}:
        fail('INVALID_EVALUATION', 'Unsupported lifecycle event or mode')
    if mode == 'FINAL' and not context.verified:
        fail('UNVERIFIED_CONTEXT', 'Final evaluation requires server-verified context')
    with localcontext() as ctx:
        ctx.prec = 80
        plan, selected = select_tariff(context, assignments, tariffs)
        gst, gst_selected = select_gst(context, gst_assignments, gst_profiles)
        profile = plan['profiles'][context.relation]
        lines = []
        disclosures = []
        raw_tax_contributions: list[tuple[int, str, Decimal]] = []
        phase = event
        def measure(key: str, *, required=True):
            value = measurements.get(key)
            if value is None:
                if required:
                    fail('MEASUREMENT_MISSING', f'Missing measurement: {key}')
                return None
            if not isinstance(value, Measurement):
                fail('INVALID_EVIDENCE', f'Malformed measurement: {key}')
            if mode == 'FINAL' and value.estimated:
                fail('UNVERIFIED_MEASUREMENT', f'Final amount cannot use estimate: {key}')
            return value

        def add_line(name, tax_class, rate, quantity: Decimal, unit, inclusion, source, estimated):
            rule = gst.tax_classes.get(tax_class)
            if rule is None:
                fail('GST_CLASS_MISSING', f'GST profile missing classification {tax_class}')
            rates = {h: exact(rule['rates'][h], f'{tax_class} {h}', max_value='100') for h in HEADS}
            amount = minor(exact(rate, f'{name} rate') * quantity)
            idx = len(lines)
            lines.append({'component':name,'tax_class':tax_class,'supply_group':rule['supply_group'],
                          'treatment':rule['treatment'],'quantity':str(quantity),'unit':unit,
                          'rate':rate,'price_mode':inclusion,'source':source,'estimated':estimated,
                          'stated_minor':amount})
            group_rate = sum(rates.values(), Decimal(0))
            for h in HEADS:
                unrounded = (Decimal(amount) * rates[h] /
                             (Decimal(100)+group_rate if inclusion == 'INCLUSIVE' else Decimal(100)))
                raw_tax_contributions.append((idx,h,unrounded))

        charging = profile['charging']
        if event == 'COMPLETED':
            if charging['basis'] == 'ENERGY_KWH':
                m = measure('energy_wh')
                quantity, unit = Decimal(m.value)/1000, 'kWh'
            elif charging['basis'] == 'TIME_MINUTE':
                m = measure('charging_seconds')
                quantity, unit = Decimal(_billable_minutes(m.value, charging['minute_rounding'])), 'minute'
            else:
                m = measure('session_completed')
                if m.value != 1:
                    fail('INVALID_SESSION_EVIDENCE', 'Verified completed-session fact must be exactly 1')
                quantity, unit = Decimal(1), 'session'
            add_line('CHARGING', charging['tax_class'], charging['rate'], quantity, unit,
                     _price_mode(charging.get('price_mode'),plan['price_mode']), m.source, m.estimated)
        elif event == 'UPFRONT':
            disclosures.append({'component':'CHARGING','state':'NOT_YET_DELIVERED',
                                'basis':charging['basis'],'rate':charging['rate'],
                                'price_mode':_price_mode(charging.get('price_mode'),plan['price_mode'])})
        for name in ('visit', 'distance'):
            item = profile.get(name)
            if not item or phase not in item['applies_on']:
                continue
            incl = _price_mode(item.get('price_mode'), plan['price_mode'])
            if name == 'visit':
                add_line('VISIT', item['tax_class'], item['rate'], Decimal(1),'visit', incl,'TRIGGERED_TARIFF_EVENT',False)
            else:
                if event == 'UPFRONT':
                    meters = item['minimum_billable_m']
                    source = 'FROZEN_DISTANCE_MINIMUM'
                    estimated = False
                else:
                    m = measure('travel_m')
                    meters = max(m.value,item['minimum_billable_m'])
                    source,estimated=m.source,m.estimated
                previous = 0
                for index, slab in enumerate(item['slabs'],1):
                    cap=slab['up_to_m']
                    covered=max(0,(min(meters,cap) if cap is not None else meters)-previous)
                    add_line(f'DISTANCE_{index}',item['tax_class'],slab['rate_per_km'],
                             Decimal(covered)/1000,'km',incl,source,estimated)
                    if cap is None:
                        break
                    previous=cap
        for extra in profile.get('extras',[]):
            if phase not in extra['applies_on']:
                continue
            if extra['basis'] == 'FIXED':
                quantity, unit, source, estimated = Decimal(1),'event','TRIGGERED_TARIFF_EVENT',False
            else:
                m=measure('waiting_seconds')
                seconds=max(0,m.value-extra.get('grace_seconds',0))
                quantity,unit,source,estimated=Decimal(_billable_minutes(seconds,extra['minute_rounding'])),'minute',m.source,m.estimated
            add_line(extra['id'], extra['tax_class'], extra['rate'],quantity,unit,
                     _price_mode(extra.get('price_mode'),plan['price_mode']),source,estimated)
        # Invoice grouping uses the approved supply group (not arbitrary UI lines).
        # Round group tax once and allocate its remainder deterministically.
        by_group: dict[str, list[tuple[int,str,Decimal]]] = {}
        for idx, h, raw in raw_tax_contributions:
            by_group.setdefault(lines[idx]['supply_group'], []).append((idx,h,raw))
        allocations: dict[tuple[int,str], int] = {}
        for group, contributions in by_group.items():
            total = sum((raw for _,_,raw in contributions), Decimal(0))
            rounded = int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))
            floors = [(idx,h,int(raw),raw-int(raw)) for idx,h,raw in contributions]
            remaining = rounded-sum(amount for _,_,amount,_ in floors)
            if not 0 <= remaining <= len(floors):
                fail('TAX_RECONCILIATION_FAILED', 'Invalid grouped tax allocation')
            for idx,h,amount,_ in floors:
                allocations[(idx,h)] = amount
            for idx,h,_,_ in sorted(floors, key=lambda row:(-row[3],row[0],HEADS.index(row[1])))[:remaining]:
                allocations[(idx,h)] += 1
        for i, row in enumerate(lines):
            amount = row.pop('stated_minor')
            heads = {h: allocations.get((i,h),0) for h in HEADS}
            tax = sum(heads.values())
            if row['price_mode'] == 'INCLUSIVE':
                net, gross = amount-tax,amount
                if net < 0:
                    fail('TAX_RECONCILIATION_FAILED', 'Included tax exceeds stated price')
            else:
                net,gross = amount,amount+tax
            if gross > MAX_MINOR:
                fail('MONEY_OVERFLOW','Gross monetary amount overflow')
            row.update(net_minor=net,tax_minor=tax,tax_heads_minor=heads,gross_minor=gross)
        totals={'net_minor':0,'tax_minor':0,'gross_minor':0,**{h.lower()+'_minor':0 for h in HEADS}}
        for l in lines:
            for fld in ('net_minor','tax_minor','gross_minor'):
                totals[fld]+=l[fld]
                if totals[fld]>MAX_MINOR:
                    fail('MONEY_OVERFLOW', 'Aggregated money exceeds integer range')
            for h in HEADS:
                totals[h.lower()+'_minor']+=l['tax_heads_minor'][h]
        if totals['gross_minor'] != totals['net_minor']+totals['tax_minor'] or totals['tax_minor']!=sum(totals[h.lower()+'_minor'] for h in HEADS):
            fail('MONEY_RECONCILIATION_FAILED','Line/total mismatch')
        return {'status': 'FINAL_CALCULATED_NOT_SETTLED' if mode == 'FINAL' else 'PREVIEW_ONLY',
                'engine_version':ENGINE_VERSION,'document_authority':'COMMERCIAL_STATEMENT_ONLY',
                'currency':'INR','event':event,
                'selection':{'tariff_key':plan['key'],'tariff_version':plan['version'],
                             'tariff_assignment_id':selected.id,'target_type':selected.target_type,
                             'gst_profile_id':gst.id,'gst_profile_version':gst.version,
                             'gst_assignment_id':gst_selected.id,'seller_ref':gst.seller_ref,
                             'legal_decision_ref':gst.legal_decision_ref,
                             'zone_id':context.zone_id,'channel':context.channel,
                             'relation':context.relation,'at':context.at.isoformat()},
                'lines':lines,'disclosures':disclosures,'totals':totals,
                'may_collect':False,'may_command_hal':False,'may_issue_tax_invoice':False}
