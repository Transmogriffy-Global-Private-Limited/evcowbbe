"""Pure contract tests for an additive extension; 60 prior tests are untouched.

When executed inside the real EVCOWBBE repository the canonical pricing engine
and topology resolver are also exercised. The local fallback smoke below tests
only compiler and contracts, not Go/Python arithmetic parity.
"""
from __future__ import annotations

import copy
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from cow.domain.zone_tariff_convergence import (
    CommercialContractError, TaxBindingUnavailable, TrustedCommercialContext,
    ZoneGSTBinding, Evidence, PublishedZoneTariff, validate_tariff_plan,
    compile_tariff_evaluation, evaluate_zone_tariff, select_zone_gst,
    select_published_zone_plan, plan_two_stage_collection,
)

NOW = datetime(2026, 11, 4, 6, 30, tzinfo=timezone.utc)


def make_plan():
    return {
        "policy_id": "ZONE_TARIFF_A", "policy_version": 3,
        "zone_id": "zone.a", "channel": "ROAMING", "currency": "INR",
        "profiles": {
            "INSIDE": {
                "charging": {"basis": "ENERGY_KWH", "rate": "10.00", "tax_class": "ENERGY"},
                "transport": {"rate": "100.00", "tax_class": "MOBILITY",
                              "applies_on": ["UPFRONT", "COMPLETED", "CANCELLED"]},
                "distance": {"minimum_billable_m": 2000, "tax_class": "MOBILITY",
                             "applies_on": ["UPFRONT", "COMPLETED", "CANCELLED"],
                             "slabs": [{"up_to_m": 3000, "rate_per_km": "5.00"},
                                       {"up_to_m": None, "rate_per_km": "8.00"}]},
                "extras": [
                    {"id": "WAITING", "basis": "WAITING_MINUTE", "rate": "2.00",
                     "minute_rounding": "CEIL_STARTED_MINUTE", "tax_class": "SERVICE",
                     "applies_on": ["COMPLETED"]},
                    {"id": "SERVICE_FEE", "basis": "FIXED", "rate": "10.00", "tax_class": "SERVICE",
                     "applies_on": ["COMPLETED"]},
                    {"id": "CANCEL_PENALTY", "basis": "FIXED", "rate": "25.00", "tax_class": "PENALTY",
                     "applies_on": ["CANCELLED"]},
                ],
            },
        },
    }


def make_context(relation="INSIDE", outside_authorized=False):
    return TrustedCommercialContext("zone.a", "ROAMING", relation, NOW,
                                    "DRIVER_ASSIGNMENT:approved-v2", "duty:123", None,
                                    outside_authorized)


def make_tax(gst="0", begin=None, end=None):
    return ZoneGSTBinding("zone.a", "ROAMING", 2, begin, end, "SELLER_TAX_PROFILE:42",
                          "LEGAL_TAX_DECISION:reviewed", {
                              k: {"approved": True, "sgst_percent": gst, "cgst_percent": gst,
                                  "igst_percent": "0"}
                              for k in ("ENERGY", "MOBILITY", "SERVICE", "PENALTY")})


def evidence(**kwargs):
    return Evidence(sources={"energy_wh": "HAL:transaction:1", "charging_seconds": "HAL:duration:1",
                             "travel_m": "ROUTE:record:1", "waiting_seconds": "JOB:timestamps:1"}, **kwargs)


def arithmetic_spy_engine(terms, inputs, tax, mode):
    """Test double only: confirms compiler inputs and basic linear arithmetic.
    Canonical integration tests (below) run existing pricing_engine when present.
    """
    total = 0
    tax_bases = {}
    lines = []
    for rule in terms["rules"]:
        obs = inputs["measurements"][rule["basis"]]
        assert obs["source"]
        assert mode != "FINAL" or not obs["estimate"]
        raw = Decimal(rule["rate"]) * Decimal(obs["quantity"]) * 100
        amt = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        total += amt
        tax_bases[rule["tax_class"]] = tax_bases.get(rule["tax_class"], 0) + amt
        lines.append({"rule_id": rule["id"], "amount_minor": amt})
    taxes = 0
    for tax_class, basis in tax_bases.items():
        p = tax[tax_class]
        assert p["approved"] is True
        for rate_key in ("sgst_percent", "cgst_percent", "igst_percent"):
            taxes += int((Decimal(basis) * Decimal(p[rate_key]) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"total_minor": total + taxes, "subtotal_minor": total, "tax_minor": taxes,
            "lines": lines, "mode": mode, "estimated": False}


class ZoneTariffContractTests(unittest.TestCase):
    def test_upfront_transport_plus_minimum_distance(self):
        r = evaluate_zone_tariff(make_plan(), make_context(), evidence(), "UPFRONT", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(r["total_minor"], 11000)
        self.assertEqual(r["phase"], "UPFRONT")
        self.assertEqual({x["rule_id"] for x in r["lines"]}, {"TRANSPORT", "DIST_1", "DIST_2"})

    def test_complete_charge_adds_actual_slabs_energy_waiting_and_static_fee(self):
        r = evaluate_zone_tariff(make_plan(), make_context(), evidence(energy_wh=5000, travel_m=4500, waiting_seconds=61),
                                 "COMPLETED", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(r["subtotal_minor"], 19100)  # 100 + (3*5 + 1.5*8) + 50 + 4 + 10
        self.assertEqual(r["tax_minor"], 0)
        self.assertEqual(r["policy_snapshot"]["policy_version"], 3)
        self.assertEqual(len(r["lines"]), 6)

    def test_distance_below_minimum_is_not_double_billed(self):
        r = evaluate_zone_tariff(make_plan(), make_context(), evidence(energy_wh=0, travel_m=500, waiting_seconds=0),
                                 "COMPLETED", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(r["subtotal_minor"], 12000)  # 100 + 10 distance minimum + 10 service

    def test_distance_exact_slab_cutover(self):
        terms, facts = compile_tariff_evaluation(make_plan(), make_context(), evidence(energy_wh=0, travel_m=3000, waiting_seconds=0), "COMPLETED")
        self.assertEqual(facts["measurements"]["DISTANCE_KM_1"]["quantity"], "3")
        self.assertEqual(facts["measurements"]["DISTANCE_KM_2"]["quantity"], "0")

    def test_cancellation_penalty_only_when_cancelled(self):
        r = evaluate_zone_tariff(make_plan(), make_context(), evidence(travel_m=2500), "CANCELLED", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(r["subtotal_minor"], 13750)
        self.assertEqual({x["rule_id"] for x in r["lines"]}, {"TRANSPORT", "DIST_1", "DIST_2", "CANCEL_PENALTY"})

    def test_no_show_has_no_invented_charge(self):
        with self.assertRaisesRegex(CommercialContractError, "No applicable"):
            compile_tariff_evaluation(make_plan(), make_context(), evidence(), "NO_SHOW")

    def test_outside_zone_denied_without_authorization(self):
        with self.assertRaisesRegex(CommercialContractError, "Outside-zone"):
            make_context("OUTSIDE")

    def test_outside_zone_requires_explicit_profile(self):
        with self.assertRaisesRegex(CommercialContractError, "No tariff profile"):
            compile_tariff_evaluation(make_plan(), make_context("OUTSIDE", True), evidence(), "UPFRONT")

    def test_outside_zone_profile_uses_same_plan_version(self):
        plan = make_plan()
        plan["profiles"]["OUTSIDE"] = copy.deepcopy(plan["profiles"]["INSIDE"])
        plan["profiles"]["OUTSIDE"]["transport"]["rate"] = "150.00"
        r = evaluate_zone_tariff(plan, make_context("OUTSIDE", True), evidence(), "UPFRONT", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(r["total_minor"], 16000)
        self.assertEqual(r["policy_snapshot"]["policy_id"], "ZONE_TARIFF_A")

    def test_fixed_per_session_primary(self):
        plan = make_plan()
        plan["profiles"]["INSIDE"]["charging"] = {"basis": "SESSION", "rate": "75.00", "tax_class": "ENERGY"}
        r = evaluate_zone_tariff(plan, make_context(), evidence(travel_m=0, waiting_seconds=0), "COMPLETED", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(r["subtotal_minor"], 19500)  # 100 + 10 + 75 + 10

    def test_time_billing_explicit_ceiling(self):
        plan = make_plan()
        plan["profiles"]["INSIDE"]["charging"] = {"basis": "TIME_MINUTE", "rate": "8.00", "tax_class": "ENERGY", "minute_rounding": "CEIL_STARTED_MINUTE"}
        r = evaluate_zone_tariff(plan, make_context(), evidence(charging_seconds=61, travel_m=0, waiting_seconds=0),
                                 "COMPLETED", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(r["subtotal_minor"], 13600)  # 100 + 10 + 16 + 10

    def test_time_billing_floor_does_not_round_up(self):
        plan = make_plan()
        plan["profiles"]["INSIDE"]["charging"] = {"basis": "TIME_MINUTE", "rate": "8.00", "tax_class": "ENERGY", "minute_rounding": "FLOOR_COMPLETED_MINUTE"}
        r = evaluate_zone_tariff(plan, make_context(), evidence(charging_seconds=61, travel_m=0, waiting_seconds=0),
                                 "COMPLETED", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(r["subtotal_minor"], 12800)

    def test_qr_plan_cannot_invent_transport_distance_charges(self):
        p = make_plan()
        p["channel"] = "ON_SITE_QR"
        with self.assertRaisesRegex(CommercialContractError, "QR plan"):
            validate_tariff_plan(p)
        p["profiles"]["INSIDE"]["transport"]["rate"] = "0"
        for slab in p["profiles"]["INSIDE"]["distance"]["slabs"]:
            slab["rate_per_km"] = "0"
        validate_tariff_plan(p)
        ctx = TrustedCommercialContext("zone.a", "ON_SITE_QR", "INSIDE", NOW, "QR:proximity-verified", site_id="charger:a")
        gst = make_tax()
        object.__setattr__(gst, "channel", "ON_SITE_QR")
        result = evaluate_zone_tariff(p, ctx, evidence(energy_wh=5000, travel_m=0, waiting_seconds=0), "COMPLETED", gst, engine=arithmetic_spy_engine)
        self.assertEqual(result["subtotal_minor"], 6000)  # Energy 50 + configured site fee 10.

    def test_bad_publication_flag_rejected(self):
        with self.assertRaises(CommercialContractError):
            PublishedZoneTariff(make_plan(), published="YES")

    def test_time_billing_without_rounding_is_invalid(self):
        p = make_plan()
        p["profiles"]["INSIDE"]["charging"] = {"basis": "TIME_MINUTE", "rate": "8.00", "tax_class": "ENERGY"}
        with self.assertRaisesRegex(CommercialContractError, "minute rounding"):
            validate_tariff_plan(p)

    def test_bad_primary_is_rejected(self):
        p = make_plan()
        p["profiles"]["INSIDE"]["charging"]["basis"] = ["ENERGY_KWH", "TIME_MINUTE"]
        with self.assertRaises(CommercialContractError):
            validate_tariff_plan(p)

    def test_exact_decimal_strings_only(self):
        for bad in (8.5, "-1", "2e3", "1.1234567", " 9", "NaN", "Infinity"):
            p = make_plan()
            p["profiles"]["INSIDE"]["charging"]["rate"] = bad
            with self.subTest(bad=bad), self.assertRaises(CommercialContractError):
                validate_tariff_plan(p)

    def test_slabs_require_strict_increase_and_open_tail(self):
        for endings in ([{"up_to_m": 10, "rate_per_km": "1"}, {"up_to_m": 10, "rate_per_km": "2"}],
                        [{"up_to_m": None, "rate_per_km": "1"}, {"up_to_m": None, "rate_per_km": "2"}],
                        [{"up_to_m": 100, "rate_per_km": "1"}]):
            p = make_plan()
            p["profiles"]["INSIDE"]["distance"]["slabs"] = endings
            with self.assertRaises(CommercialContractError):
                validate_tariff_plan(p)

    def test_duplicate_extra_ids_rejected(self):
        p = make_plan()
        p["profiles"]["INSIDE"]["extras"].append(copy.deepcopy(p["profiles"]["INSIDE"]["extras"][0]))
        with self.assertRaisesRegex(CommercialContractError, "Duplicate"):
            validate_tariff_plan(p)

    def test_unverified_measurement_is_not_authoritative(self):
        with self.assertRaisesRegex(CommercialContractError, "source"):
            compile_tariff_evaluation(make_plan(), make_context(), Evidence(energy_wh=1000, travel_m=3000, waiting_seconds=0, sources={}), "COMPLETED")

    def test_final_rejects_estimated_measurement(self):
        ev = evidence(energy_wh=1000, travel_m=3000, waiting_seconds=0, estimated=frozenset({"energy_wh"}))
        with self.assertRaisesRegex(CommercialContractError, "estimated"):
            compile_tariff_evaluation(make_plan(), make_context(), ev, "COMPLETED")

    def test_missing_hal_energy_is_not_fabricated(self):
        with self.assertRaisesRegex(CommercialContractError, "energy_wh"):
            compile_tariff_evaluation(make_plan(), make_context(), evidence(travel_m=2500, waiting_seconds=0), "COMPLETED")

    def test_context_requires_roaming_driver_or_qr_site(self):
        with self.assertRaises(CommercialContractError):
            TrustedCommercialContext("zone.a", "ROAMING", "INSIDE", NOW, "trusted")
        with self.assertRaises(CommercialContractError):
            TrustedCommercialContext("zone.a", "ON_SITE_QR", "INSIDE", NOW, "trusted", driver_duty_id="abc")
        TrustedCommercialContext("zone.a", "ON_SITE_QR", "INSIDE", NOW, "trusted", site_id="charger:a")

    def test_outside_authorization_must_be_real_bool(self):
        with self.assertRaisesRegex(CommercialContractError, "boolean"):
            TrustedCommercialContext("zone.a", "ROAMING", "OUTSIDE", NOW, "trusted", "duty", outside_authorized="yes")

    def test_cannot_bill_waiting_before_service(self):
        p = make_plan()
        p["profiles"]["INSIDE"]["extras"][0]["applies_on"].append("UPFRONT")
        with self.assertRaisesRegex(CommercialContractError, "Waiting cannot"):
            validate_tariff_plan(p)

    def test_context_rejects_naive_time(self):
        with self.assertRaises(CommercialContractError):
            TrustedCommercialContext("zone.a", "ROAMING", "INSIDE", datetime(2026, 11, 4), "trusted", "duty")

    def test_tax_applies_to_all_classes_with_approved_rates(self):
        r = evaluate_zone_tariff(make_plan(), make_context(), evidence(), "UPFRONT", make_tax("9"), engine=arithmetic_spy_engine)
        self.assertEqual(r["subtotal_minor"], 11000)
        self.assertEqual(r["tax_minor"], 1980)
        self.assertEqual(r["total_minor"], 12980)

    def test_gst_wrong_zone_not_reused(self):
        gst = make_tax()
        object.__setattr__(gst, "zone_id", "zone.other")  # Simulate bad repository binding
        with self.assertRaises(TaxBindingUnavailable):
            evaluate_zone_tariff(make_plan(), make_context(), evidence(), "UPFRONT", gst, engine=arithmetic_spy_engine)

    def test_zone_gst_cannot_cross_charging_channels(self):
        with self.assertRaises(TaxBindingUnavailable):
            select_zone_gst([make_tax()], "zone.a", "ON_SITE_QR", NOW)
        ctx = TrustedCommercialContext("zone.a", "ON_SITE_QR", "INSIDE", NOW, "SITE:verified", site_id="site:a")
        p = make_plan()
        p["channel"] = "ON_SITE_QR"
        p["profiles"]["INSIDE"]["transport"]["rate"] = "0"
        for slab in p["profiles"]["INSIDE"]["distance"]["slabs"]:
            slab["rate_per_km"] = "0"
        with self.assertRaises(TaxBindingUnavailable):
            evaluate_zone_tariff(p, ctx, evidence(energy_wh=5000, travel_m=0, waiting_seconds=0), "COMPLETED", make_tax(), engine=arithmetic_spy_engine)

    def test_tax_missing_or_ambiguous_at_boundary(self):
        a = make_tax(begin=NOW - timedelta(days=1), end=NOW)
        b = make_tax(begin=NOW, end=NOW + timedelta(days=1))
        self.assertIs(select_zone_gst([a, b], "zone.a", "ROAMING", NOW), b)
        with self.assertRaises(TaxBindingUnavailable):
            select_zone_gst([a, b], "zone.a", "ROAMING", NOW + timedelta(days=2))
        with self.assertRaises(TaxBindingUnavailable):
            select_zone_gst([b, b], "zone.a", "ROAMING", NOW)

    def test_gst_mixed_sgst_igst_rejected(self):
        p = make_tax()
        p.tax_profiles["ENERGY"]["sgst_percent"] = "9"
        p.tax_profiles["ENERGY"]["igst_percent"] = "18"
        with self.assertRaises(TaxBindingUnavailable):
            ZoneGSTBinding(p.zone_id, "ROAMING", 3, None, None, p.seller_tax_ref, p.legal_decision_ref, p.tax_profiles)

    def test_tax_binding_requires_string_legal_identity(self):
        with self.assertRaises(TaxBindingUnavailable):
            ZoneGSTBinding("zone.a", "ROAMING", 1, None, None, [], "approved", {"ENERGY": {"approved": True,
                           "sgst_percent": "0", "cgst_percent": "0", "igst_percent": "0"}})

    def test_gst_no_legal_decision_rejected(self):
        with self.assertRaises(TaxBindingUnavailable):
            ZoneGSTBinding("zone.a", "ROAMING", 1, None, None, "seller", "", {"ENERGY": {"approved": True,
                             "sgst_percent": "0", "cgst_percent": "0", "igst_percent": "0"}})

    def test_tax_snapshot_not_shared_with_mutable_registry(self):
        p = make_plan(); g = make_tax()
        r = evaluate_zone_tariff(p, make_context(), evidence(), "UPFRONT", g, engine=arithmetic_spy_engine)
        p["profiles"]["INSIDE"]["transport"]["rate"] = "900"
        g.tax_profiles["ENERGY"]["sgst_percent"] = "9"
        self.assertEqual(r["policy_snapshot"]["profiles"]["INSIDE"]["transport"]["rate"], "100.00")
        self.assertEqual(r["tax_binding"]["tax_profiles"]["ENERGY"]["sgst_percent"], "0")

    def test_compiler_has_unique_rule_ids_and_bases(self):
        rules, facts = compile_tariff_evaluation(make_plan(), make_context(), evidence(energy_wh=2000, travel_m=3500, waiting_seconds=62), "COMPLETED")
        self.assertEqual(len(rules["rules"]), len({x["id"] for x in rules["rules"]}))
        self.assertEqual(len(facts["measurements"]), len({x["basis"] for x in rules["rules"]}))

    def test_full_snapshot_json_shape_is_immutable_copy(self):
        plan = make_plan()
        result = evaluate_zone_tariff(plan, make_context(), evidence(), "UPFRONT", make_tax(), engine=arithmetic_spy_engine)
        self.assertEqual(result["commercial_context"]["driver_duty_id"], "duty:123")
        self.assertEqual(result["tax_binding"]["legal_decision_ref"], "LEGAL_TAX_DECISION:reviewed")


class TemporalAndCollectionTests(unittest.TestCase):
    def test_real_topology_if_present(self):
        try:
            from cow.services.pricing_topology import resolve
        except ModuleNotFoundError:
            self.skipTest("Canonical pricing_topology.py not present outside repository")
        p1 = make_plan(); p2 = make_plan(); p3 = make_plan()
        p1["policy_version"] = 1
        p2["policy_version"] = 2
        p3["policy_version"] = 3
        catalog = [PublishedZoneTariff(p1),
                   PublishedZoneTariff(p2, NOW-timedelta(days=1)),
                   PublishedZoneTariff(p3, NOW-timedelta(hours=1), NOW+timedelta(hours=1))]
        winner = select_published_zone_plan(catalog, make_context(), resolver=resolve)
        self.assertEqual(winner["policy_version"], 3)

    def test_real_pricing_engine_if_present(self):
        try:
            from cow.services.pricing_engine import evaluate
        except ModuleNotFoundError:
            self.skipTest("Canonical pricing_engine.py not present outside repository")
        r = evaluate_zone_tariff(make_plan(), make_context(), evidence(energy_wh=5000, travel_m=4500, waiting_seconds=61),
                                 "COMPLETED", make_tax(), engine=evaluate)
        self.assertEqual(r["total_minor"], 19100)
        self.assertEqual(sum(x["amount_minor"] for x in r["lines"]), r["total_minor"])

    def test_upfront_and_final_debit(self):
        j = str(uuid4())
        x = plan_two_stage_collection(j, 11000, 11000, 19100, posted_upfront_receipt="wallet_tx:7")
        self.assertEqual((x.additional_debit_minor, x.customer_credit_minor), (8100, 0))
        self.assertEqual(x.upfront_key, f"cow:upfront:{j}")
        self.assertEqual(x.final_key, f"cow:final:{j}")
        self.assertEqual(x.upfront_debit_minor + x.additional_debit_minor - x.customer_credit_minor, x.final_total_minor)

    def test_final_credit_not_negative_debit(self):
        x = plan_two_stage_collection(str(uuid4()), 11000, 11000, 9000, posted_upfront_receipt="posted")
        self.assertEqual((x.additional_debit_minor, x.customer_credit_minor), (0, 2000))

    def test_equal_final_requires_no_additional_debit(self):
        x = plan_two_stage_collection(str(uuid4()), 11000, 11000, 11000, posted_upfront_receipt="posted")
        self.assertEqual((x.additional_debit_minor, x.customer_credit_minor), (0, 0))

    def test_missing_upfront_record_requires_reconciliation(self):
        with self.assertRaises(CommercialContractError):
            plan_two_stage_collection(str(uuid4()), 11000, 0, 19100, posted_upfront_receipt=None)

    def test_partial_upfront_collection_rejected(self):
        with self.assertRaises(CommercialContractError):
            plan_two_stage_collection(str(uuid4()), 11000, 2000, 19100, posted_upfront_receipt="partial")

    def test_invalid_money_inputs_rejected(self):
        for bad in (-1, 1.2, True, 2**63):
            with self.subTest(bad=bad), self.assertRaises(CommercialContractError):
                plan_two_stage_collection(str(uuid4()), 11000, 11000, bad, posted_upfront_receipt="posted")

    def test_unposted_with_forged_receipt_rejected(self):
        with self.assertRaises(CommercialContractError):
            plan_two_stage_collection(str(uuid4()), 0, 0, 100, posted_upfront_receipt="claim")

    def test_zero_upfront_plan_allowed(self):
        x = plan_two_stage_collection(str(uuid4()), 0, 0, 500, posted_upfront_receipt=None)
        self.assertEqual(x.additional_debit_minor, 500)

    def test_stable_keys_across_retries(self):
        j = str(uuid4())
        args = (j, 11000, 11000, 19100)
        a = plan_two_stage_collection(*args, posted_upfront_receipt="receipt")
        b = plan_two_stage_collection(*args, posted_upfront_receipt="receipt")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
