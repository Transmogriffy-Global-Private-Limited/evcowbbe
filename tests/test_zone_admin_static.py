"""Offline Sprint 7 wiring, migration and safety regressions (no Django setup)."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ZoneAdminStaticTests(unittest.TestCase):
    def test_source_compiles(self):
        for path in ('cow/admin/zones.py', 'cow/services/zone_admin.py',
                     'cow/migrations/0009_zone_admin_operations.py',
                     'tests/integration/test_zone_admin_pg.py'):
            ast.parse((ROOT/path).read_text(encoding='utf-8'), filename=path)

    def test_single_tariff_publication_ledger(self):
        svc = (ROOT/'cow/services/zone_admin.py').read_text(encoding='utf-8')
        self.assertIn('PricingPolicy.objects.create', svc)
        self.assertIn('ZoneTariffPayload.objects.create', svc)
        self.assertIn('validate_zone_publication', svc)
        self.assertNotIn('ChargeWallet', svc)
        self.assertNotIn('HALCommand.objects.create', svc)
        trigger = (ROOT/'cow/migrations/0009_zone_admin_operations.py').read_text(encoding='utf-8')
        self.assertIn('BASE_GUARD', trigger)
        self.assertIn('cow_zonetariffpayload', trigger)
        self.assertIn('cow_zone_single_published_key', trigger)

    def test_wiring_and_idempotency(self):
        router = (ROOT/'cow/admin/router.py').read_text(encoding='utf-8')
        self.assertIn('register_zone_routes(router)', router)
        api = (ROOT/'cow/admin/zones.py').read_text(encoding='utf-8')
        self.assertIn('check_body(request.body)', api)
        self.assertIn('idempotency.execute', api)
        self.assertIn('simulation_only', (ROOT/'cow/services/zone_admin.py').read_text())

    def test_runner_uses_latest_migration(self):
        src = (ROOT/'scripts/run_pg_tests.py').read_text(encoding='utf-8')
        self.assertIn('MIGRATIONS_0009', src)
        self.assertIn('0009_zone_admin_operations', src)
        self.assertIn('TRIGGERS_0008, FUNCTIONS_0008', src)


if __name__ == '__main__':
    unittest.main()
