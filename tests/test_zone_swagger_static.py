"""Source-level Swagger invariants; no live database required."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ZoneOnlySwaggerStatic(unittest.TestCase):
    def test_models_schema_and_openapi_source_parse(self):
        for name in ('cow/admin/zone_contracts.py','cow/admin/zone_openapi.py',
                     'cow/admin/zones.py','scripts/verify_zone_swagger.py'):
            ast.parse((ROOT/name).read_text(encoding='utf-8'), filename=name)

    def test_only_zone_tariff_mode_on_public_admin_api(self):
        controls=(ROOT/'cow/admin/controls.py').read_text(encoding='utf-8')
        self.assertEqual(controls.count('include_in_schema=False'),6)
        self.assertEqual(controls.count('GENERIC_PRICING_RETIRED'),6)
        zone=(ROOT/'cow/admin/zones.py').read_text(encoding='utf-8')
        self.assertIn('plan: ZoneTariffPlan',zone)
        self.assertIn('TariffEnvelope',zone)
        self.assertIn("response['ETag']",zone)
        self.assertNotIn('response={200: dict}',zone)
        self.assertNotIn('response={201: dict',zone)

    def test_legacy_customer_billing_does_not_bypass_zone_authority(self):
        customer=(ROOT/'cow/users/charging.py').read_text(encoding='utf-8')
        jobs=(ROOT/'cow/services/jobs.py').read_text(encoding='utf-8')
        self.assertEqual(customer.count('include_in_schema=False'),2)
        self.assertEqual(customer.count('LEGACY_QUOTE_RETIRED'),2)
        self.assertEqual(jobs.count('LEGACY_QUOTE_RETIRED'),2)
        self.assertNotIn('quote_amount(',jobs)
        self.assertIn('operation_id="cow_api_quote_request"',customer)
        self.assertIn('operation_id="cow_api_accept_quote"',customer)

    def test_frontend_documentation_wired_without_false_routes(self):
        fe=(ROOT/'cow/fe_openapi.py').read_text(encoding='utf-8')
        self.assertIn('enrich_zone_operation(path, method.lower(), operation)',fe)
        self.assertIn('zone_openapi_intro()',fe)
        self.assertIn('x-commercial-tariff-mode',fe)
        self.assertNotIn('"17 | Admin pricing policies"',fe)

if __name__ == '__main__': unittest.main()
