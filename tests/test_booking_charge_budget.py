"""Source-only future tests. NOT run by installer or in this handoff.

Covers owner-approved MI/ME/buffer arithmetic and per-physical-session cap.
"""
import pytest
from cow.domain.booking_charge_budget import (
    booking_affordability, determine_cap, minimum_for_distance,
    validate_minimum_schedule, BookingBudgetError,
)


def test_minimum_plus_extra_wh_and_buffer():
    d=booking_affordability(50000,0,15000,20000,5000)
    assert (d.extra_charging_paise,d.charging_budget_paise)==(10000,30000)
    cap=determine_cap(price_gross_paise=lambda q:2*q,
        money_state=d,previously_completed_wh=0,
        maximum_total_wh=50000,maximum_per_session_wh=50000)
    assert (cap.mandatory_wh,cap.extra_wh,cap.physical_session_wh)==(10000,5000,15000)


def test_exact_funding_has_minimum_but_no_extra():
    d=booking_affordability(40000,0,15000,20000,5000)
    assert d.extra_charging_paise==0
    cap=determine_cap(price_gross_paise=lambda q:2*q,
        money_state=d,previously_completed_wh=0,
        maximum_total_wh=50000,maximum_per_session_wh=50000)
    assert cap.physical_session_wh==10000


def test_negative_available_cannot_book():
    with pytest.raises(BookingBudgetError):
        booking_affordability(35000,0,15000,20000,5000)


def test_other_reservation_reduces_extra_before_limit():
    d=booking_affordability(50000,3000,15000,20000,5000)
    assert d.charging_budget_paise==27000


def test_second_session_only_receives_residual_wh():
    d=booking_affordability(50000,0,15000,20000,5000)
    cap=determine_cap(price_gross_paise=lambda q:2*q,
        money_state=d,previously_completed_wh=6000,
        maximum_total_wh=50000,maximum_per_session_wh=50000)
    assert cap.physical_session_wh==9000
    assert cap.previous_wh+cap.physical_session_wh==15000


def test_second_session_after_verified_topup_can_grow():
    d=booking_affordability(60000,0,15000,20000,5000)
    cap=determine_cap(price_gross_paise=lambda q:2*q,
        money_state=d,previously_completed_wh=6000,
        maximum_total_wh=50000,maximum_per_session_wh=50000)
    assert cap.physical_session_wh==14000


def test_nonlinear_rounding_uses_cumulative_price_not_sum_of_inverses():
    # Monotone stepped gross price rounds each 4 Wh as a canonical amount.
    d=booking_affordability(500,0,100,200,50)
    price=lambda q:(q+3)//4*5
    cap=determine_cap(price_gross_paise=price,money_state=d,
        previously_completed_wh=0,maximum_total_wh=500,maximum_per_session_wh=500)
    assert price(cap.cumulative_wh)<=d.charging_budget_paise
    assert price(cap.cumulative_wh+1)>d.charging_budget_paise
    assert cap.mandatory_wh+cap.extra_wh==cap.cumulative_wh


def test_replayed_consumption_must_not_get_full_cap_again():
    d=booking_affordability(50000,0,15000,20000,5000)
    with pytest.raises(BookingBudgetError):
        determine_cap(price_gross_paise=lambda q:2*q,money_state=d,
            previously_completed_wh=15000,maximum_total_wh=50000,
            maximum_per_session_wh=50000)


def test_slab_boundaries_and_last_tier():
    slabs=[{'up_to_m':5000,'min_gross_paise':10000},
        {'up_to_m':15000,'min_gross_paise':20000},
        {'up_to_m':None,'min_gross_paise':30000}]
    assert minimum_for_distance(slabs,5000)==10000
    assert minimum_for_distance(slabs,5001)==20000
    assert minimum_for_distance(slabs,25000)==30000


def test_tier_reordering_and_nonmonotone_minimum_fail_closed():
    with pytest.raises(BookingBudgetError):
        validate_minimum_schedule([
            {'up_to_m':5000,'min_gross_paise':20000},
            {'up_to_m':None,'min_gross_paise':10000}])


def test_boolean_wallet_input_is_not_valid_money():
    with pytest.raises(BookingBudgetError):
        booking_affordability(True,0,0,0,0)
