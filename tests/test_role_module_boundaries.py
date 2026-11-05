"""Structural role API isolation checks with no DB requirements."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RoleModuleBoundaries(unittest.TestCase):
    def test_exact_one_django_app_and_schema(self):
        assert (ROOT / "cow/models.py").is_file()
        assert not (ROOT / "cow/users/models.py").exists()
        assert not (ROOT / "cow/drivers/models.py").exists()
        assert not (ROOT / "cow/admin/models.py").exists()
        assert not (ROOT / "cow/common/models.py").exists()
        assert not (ROOT / "cow/users/migrations").exists()
        assert not (ROOT / "cow/drivers/migrations").exists()
        assert not (ROOT / "cow/admin/migrations").exists()
        assert not (ROOT / "cow/common/migrations").exists()

    def test_router_registration_is_explicit_and_central(self):
        api = (ROOT / "cow/api.py").read_text()
        user = (ROOT / "cow/users/router.py").read_text()
        driver = (ROOT / "cow/drivers/router.py").read_text()
        admin = (ROOT / "cow/admin/router.py").read_text()
        self.assertIn('api.add_router("/v1/app", app)', api)
        self.assertIn('api.add_router("/v1/driver", driver)', api)
        self.assertIn('api.add_router("/v1/admin", admin)', api)
        self.assertIn('build_users_router(', api)
        self.assertIn('build_drivers_router(', api)
        self.assertIn('build_admin_router(', api)
        self.assertIn('mount_identity(router, "app")', user)
        self.assertIn('mount_identity(router, "driver")', driver)
        self.assertIn('mount_identity(router, "admin")', admin)
        self.assertNotIn('@app.', api)
        self.assertNotIn('@admin.', api)
        self.assertIn('from cow.admin.fleet import fleet_router', api)

    def test_no_duplicate_business_tables_or_routers(self):
        for role in ('users', 'drivers', 'admin', 'common'):
            for p in (ROOT / 'cow' / role).rglob('*.py'):
                tree = ast.parse(p.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotEqual(node.func.id, 'NinjaAPI')
                    if isinstance(node, ast.ClassDef):
                        self.assertNotIn(node.name, ('Account', 'PricingPolicy', 'ServiceZone', 'ServiceJob'))

    def test_legacy_ids_pinned_after_move(self):
        s = (ROOT / 'cow/users/charging.py').read_text()
        for suffix in ('request_service', 'quote_request', 'accept_quote', 'get_job'):
            self.assertIn(f'operation_id="cow_api_{suffix}"', s)
        s = (ROOT / 'cow/users/master.py').read_text()
        jobs = (ROOT / 'cow/admin/jobs.py').read_text()
        for suffix in ('admin_jobs', 'admin_get_job', 'start_charging'):
            self.assertIn(f'operation_id="cow_api_{suffix}"', jobs)
        self.assertEqual(jobs.count('@router.'), 3)
        self.assertEqual(s.count('@router.'), 13)


if __name__ == '__main__':
    unittest.main()
