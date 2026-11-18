"""Pure booking guarantee and energy-affordability mathematics.

No DB, payment, permission, geolocation, or protocol authority exists here.
All monetary values are gross INR paise, physical energy integer Wh.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

MAX_MINOR = (1 << 63) - 1
MAX_WH = (1 << 63) - 1


class BookingBudgetError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def money(value: int, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_MINOR:
        raise BookingBudgetError('INVALID_' + label.upper())
    return value


def wh(value: int, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_WH:
        raise BookingBudgetError('INVALID_' + label.upper())
    return value


def safe_total(*values: int) -> int:
    total = 0
    for n in values:
        total += money(n, 'money')
        if total > MAX_MINOR:
            raise BookingBudgetError('MONEY_OVERFLOW')
    return total


def validate_minimum_schedule(slabs: object) -> tuple[tuple[int | None, int], ...]:
    """Version-pinned distance tiers: disjoint increasing bounds, final unbounded.

    Each amount is a *gross* minimum purchase, not a fee for distance. The
    priced kWh still comes exclusively from the canonical tariff evaluator.
    """
    if not isinstance(slabs, list) or not 1 <= len(slabs) <= 64:
        raise BookingBudgetError('MINIMUM_SCHEDULE_INVALID')
    result = []
    last_bound = 0
    last_amount = -1
    for i, item in enumerate(slabs):
        if not isinstance(item, dict) or set(item) != {'up_to_m', 'min_gross_paise'}:
            raise BookingBudgetError('MINIMUM_TIER_INVALID')
        boundary, minimum = item['up_to_m'], money(item['min_gross_paise'], 'minimum')
        if minimum <= 0 or minimum < last_amount:
            raise BookingBudgetError('MINIMUM_TIER_NOT_MONOTONE')
        if boundary is None:
            if i != len(slabs) - 1:
                raise BookingBudgetError('MINIMUM_UNBOUNDED_NOT_LAST')
        elif type(boundary) is not int or boundary <= last_bound or boundary > MAX_WH or i == len(slabs) - 1:
            raise BookingBudgetError('MINIMUM_TIER_BOUND_INVALID')
        else:
            last_bound = boundary
        last_amount = minimum
        result.append((boundary, minimum))
    if result[-1][0] is not None:
        raise BookingBudgetError('MINIMUM_LAST_TIER_MISSING')
    return tuple(result)


def minimum_for_distance(slabs: object, approved_distance_m: int) -> int:
    wh(approved_distance_m, 'approved_distance_m')
    for boundary, minimum in validate_minimum_schedule(slabs):
        if boundary is None or approved_distance_m <= boundary:
            return minimum
    raise BookingBudgetError('MINIMUM_DISTANCE_UNCOVERED')


@dataclass(frozen=True)
class BookingAffordability:
    mandatory_noncharging_paise: int
    mandatory_charging_paise: int
    protected_buffer_paise: int
    unrelated_holds_paise: int
    wallet_balance_paise: int
    charging_budget_paise: int
    extra_charging_paise: int


def booking_affordability(wallet_balance_paise: int, unrelated_holds_paise: int,
                          mandatory_noncharging_paise: int, mandatory_charging_paise: int,
                          protected_buffer_paise: int) -> BookingAffordability:
    w = money(wallet_balance_paise, 'wallet_balance')
    r = money(unrelated_holds_paise, 'other_holds')
    f = money(mandatory_noncharging_paise, 'fixed_fees')
    m = money(mandatory_charging_paise, 'minimum_charging')
    b = money(protected_buffer_paise, 'buffer')
    mandatory = safe_total(f, m, b, r)
    if w < mandatory:
        raise BookingBudgetError('BOOKING_WALLET_INSUFFICIENT')
    extra = w - mandatory
    budget = safe_total(m, extra)  # M + (W - R - F - M - B)
    if budget != w - r - f - b:
        raise BookingBudgetError('BOOKING_BUDGET_RECONCILIATION')
    return BookingAffordability(f, m, b, r, w, budget, extra)


@dataclass(frozen=True)
class ChargingCap:
    mandatory_wh: int
    extra_wh: int
    cumulative_wh: int
    previous_wh: int
    physical_session_wh: int
    charging_budget_paise: int


def _invert(priced_gross_paise: Callable[[int], int], budget: int, ceiling_wh: int) -> int:
    """Maximum affordable integer Wh using canonical frozen tariff price(Q).

    The caller proves a nonnegative-rate fixed ENERGY_KWH tariff. Such a
    tariff with canonical GST rounding is nondecreasing in Wh. Do not use
    this with discount tiers, time tariffs, or power-derived approximations.
    """
    money(budget, 'budget')
    wh(ceiling_wh, 'ceiling_wh')
    if not callable(priced_gross_paise):
        raise BookingBudgetError('PRICE_FUNCTION_MISSING')

    def price(q):
        value = priced_gross_paise(q)
        return money(value, 'canonical_gross')

    if price(0) > budget:
        raise BookingBudgetError('CHARGING_FIXED_BASE_UNAFFORDABLE')
    if price(ceiling_wh) <= budget:
        return ceiling_wh
    low, high = 0, ceiling_wh
    while high - low > 1:
        middle = low + (high - low) // 2
        if price(middle) <= budget:
            low = middle
        else:
            high = middle
    if price(low) > budget or price(high) <= budget:
        raise BookingBudgetError('PRICE_INVERSION_INCONSISTENT')
    return low


def determine_cap(*, price_gross_paise: Callable[[int], int],
                  money_state: BookingAffordability,
                  previously_completed_wh: int, maximum_total_wh: int,
                  maximum_per_session_wh: int) -> ChargingCap:
    prior = wh(previously_completed_wh, 'previous_wh')
    wh(maximum_total_wh, 'maximum_total_wh')
    wh(maximum_per_session_wh, 'maximum_per_session_wh')
    if maximum_total_wh == 0 or maximum_per_session_wh == 0 or prior > maximum_total_wh:
        raise BookingBudgetError('ENERGY_CAP_EXHAUSTED')
    mandatory_wh = _invert(price_gross_paise, money_state.mandatory_charging_paise,
                           maximum_total_wh)
    max_wh = _invert(price_gross_paise, money_state.charging_budget_paise,
                     maximum_total_wh)
    if max_wh < mandatory_wh or max_wh <= prior:
        raise BookingBudgetError('ENERGY_CAP_EXHAUSTED')
    incremental = min(max_wh - prior, maximum_per_session_wh)
    if incremental <= 0:
        raise BookingBudgetError('ENERGY_CAP_EXHAUSTED')
    if price_gross_paise(max_wh) > money_state.charging_budget_paise:
        raise BookingBudgetError('ENERGY_PRICE_EXCEEDS_BUDGET')
    return ChargingCap(mandatory_wh, max_wh - mandatory_wh, max_wh, prior,
                       incremental, money_state.charging_budget_paise)
