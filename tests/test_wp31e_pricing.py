"""Go engine parity vectors; no Django or PostgreSQL dependency."""
import unittest
from datetime import UTC, datetime, timedelta
from cow.services.pricing_engine import (evaluate, PricingError, MissingEvidence,
                                         TaxUnavailable, AmountOverflow, NoApplicablePolicy)
from cow.services.pricing_topology import resolve, validate_topology, TemporalConflict
from cow.services.pricing_wire import check_body, InvalidPricingBody


def base(rate="16.91"):
    return {"policy_id":"policy-1", "policy_version":1,"currency":"INR","rules":[{
        "id":"ENERGY","kind":"BASE","component":"CHARGING_ENERGY",
        "basis":"METER_KWH","rate":rate,"tax_class":"EV_SERVICE"}]}

def facts(q="7.2", estimated=False):
    return {"measurements":{"METER_KWH":{"quantity":q,"source":"HAL_TX_1","estimate":estimated}}}

def tax(a="9",b="9",c="0"):
    return {"EV_SERVICE":{"approved":True,"sgst_percent":a,"cgst_percent":b,"igst_percent":c}}

class PricingParity(unittest.TestCase):
    def test_go_exact_16_91_7_2(self):
        result = evaluate(base(), facts(), tax(), "FINAL")
        self.assertEqual((result["subtotal_minor"],result["tax_minor"],result["total_minor"]),(12175,2192,14367))
        self.assertEqual(sum(x["amount_minor"] for x in result["lines"]),result["total_minor"])

    def test_go_zero_tax_mobile_rules(self):
        terms={"policy_id":"mobile-p","policy_version":3,"currency":"INR","rules":[
           {"id":"TRAVEL","kind":"BASE","component":"TRAVEL_DISTANCE","basis":"ROUTE_KM","rate":"12","tax_class":"EV_SERVICE"},
           {"id":"VISIT","kind":"BASE","component":"VISIT","basis":"VISITS","rate":"100","tax_class":"EV_SERVICE"},
           {"id":"VISIT_MULT","kind":"MULTIPLIER","target":"VISIT","rate":"120","priority":1},
           {"id":"VISIT_DISC","kind":"DISCOUNT_PERCENT","target":"VISIT","rate":"10","priority":2}]}
        v=evaluate(terms,{"measurements":{"VISITS":{"quantity":"1","source":"JOB_1"},"ROUTE_KM":{"quantity":"5.5","source":"ROUTE_1"}}},tax("0","0","0"),"FINAL")
        self.assertEqual((v["subtotal_minor"],v["total_minor"],len(v["lines"])),(17400,17400,4))

    def test_override_discounts(self):
        terms=base("10")
        terms["rules"] += [{"id":"OVERRIDE","kind":"OVERRIDE_RATE","target":"ENERGY","rate":"12"},{"id":"DISCOUNT","kind":"DISCOUNT_PERCENT","target":"ENERGY","rate":"25"}]
        self.assertEqual(evaluate(terms,facts("2"),tax("0","0","0"),"FINAL")["subtotal_minor"],1800)

    def test_unknown_or_estimated_evidence(self):
        with self.assertRaises(MissingEvidence):evaluate(base("10"),facts("2",True),tax("0","0","0"),"FINAL")
        self.assertTrue(evaluate(base("10"),facts("2",True),tax("0","0","0"),"PREVIEW")["estimated"])
        p=base("10")
        p["rules"][0]["conditions"]={"SERVICE_CLASS":"FAST"}
        with self.assertRaises(MissingEvidence):evaluate(p,facts("1"),None)
        with self.assertRaises(NoApplicablePolicy):evaluate(p,{"conditions":{"SERVICE_CLASS":"NORMAL"}},None)

    def test_tax_requires_approval_and_one_treatment(self):
        for profile in (None,tax("9","9","18"),{"EV_SERVICE":{"approved":False,"sgst_percent":"0","cgst_percent":"0","igst_percent":"0"}}):
            with self.subTest(profile=profile),self.assertRaises(TaxUnavailable):evaluate(base("10"),facts("1"),profile,"FINAL")

    def test_half_paise_and_overflow(self):
        self.assertEqual(evaluate(base("0.005"),facts("1"),tax("0","0","0"),"FINAL")["total_minor"],1)
        with self.assertRaises(AmountOverflow):evaluate(base("999999999999"),facts("999999999999"),tax("0","0","0"),"FINAL")

    def test_duplicate_json_object_keys_rejected_recursively(self):
        good = b'{"scope":{"kind":"GLOBAL","key":"DEFAULT"}}'
        self.assertEqual(check_body(good)["scope"]["key"],"DEFAULT")
        for bad in (b'{"scope":{"key":"A","key":"B"}}',
                    b'{"reason":"one","reason":"two"}',
                    b'{"rules":[{"rate":1,"rate":2}]}',
                    b'{"x":NaN}', b'[]', b'{} ' + b' ' * 32768):
            with self.subTest(bad=bad[:60]), self.assertRaises(InvalidPricingBody):
                check_body(bad)

    def test_bad_policy_rules(self):
        mutations=[
            lambda t:t["rules"][0].update(rate="16.91/kWh"),
            lambda t:t["rules"][0].update(rate="-1"),
            lambda t:t["rules"].append(t["rules"][0].copy()),
            lambda t:t["rules"].append({"id":"DUP","kind":"BASE","component":"CHARGING_ENERGY","basis":"VISITS","tax_class":"EV_SERVICE","rate":"1"}),
            lambda t:t["rules"].append({"id":"X","kind":"DISCOUNT_PERCENT","target":"GHOST","rate":"5"}),
            lambda t:t["rules"].append({"id":"X","kind":"DISCOUNT_PERCENT","target":"ENERGY","rate":"101"}),
            lambda t:t["rules"][0].update(conditions={"unknown field":"YES"}),
        ]
        for mutate in mutations:
            p=base("10");mutate(p)
            with self.assertRaises(PricingError):evaluate(p,facts("1"),tax())

    def test_temporal_nested_open_and_precedence(self):
        now=datetime(2026,10,31,tzinfo=UTC)
        scope={"kind":"GLOBAL","key":"DEFAULT"}
        def policy(i,s,e,scope=scope):
            return {"scope":scope,"starts_at":s,"ends_at":e,"active":True,"terms":{"policy_id":i,"policy_version":1,"currency":"INR","rules":base()["rules"]}}
        root=policy("ROOT",None,None)
        open1=policy("OPEN",now-timedelta(days=10),None)
        outer=policy("OUTER",now-timedelta(days=5),now+timedelta(days=5))
        inner=policy("INNER",now-timedelta(days=1),now+timedelta(days=1))
        self.assertTrue(validate_topology([root,open1,outer,inner]))
        self.assertEqual(resolve([root,open1,outer,inner],[scope],now)["terms"]["policy_id"],"INNER")
        with self.assertRaises(TemporalConflict):
            validate_topology([root,open1,outer,policy("CROSS",now,now+timedelta(days=7))])
        with self.assertRaises(TemporalConflict):
            validate_topology([root,policy("ROOT2",None,None)])
        with self.assertRaises(TemporalConflict):
            validate_topology([open1,policy("OPEN2",now-timedelta(days=10),None)])
        with self.assertRaises(NoApplicablePolicy):
            resolve([root],[{"kind":"GLOBAL","key":"UNKNOWN"}],now)

if __name__=="__main__":unittest.main()
