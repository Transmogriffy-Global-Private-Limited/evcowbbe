"""Offline source/DTO invariants; no DB or Django settings needed."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]

class Sprint9AdminManagementSource(unittest.TestCase):
    def test_new_sources_parse(self):
        for path in ('cow/services/admin_directory.py','cow/services/admin_groups.py',
                     'cow/admin/management.py','tests/integration/test_admin_management_pg.py'):
            ast.parse((ROOT / path).read_text(encoding='utf-8'), filename=path)

    def test_route_registration_and_no_schema_changes(self):
        router = (ROOT / 'cow/admin/router.py').read_text(encoding='utf-8')
        self.assertIn('register_admin_management(router)', router)
        source = (ROOT / 'cow/admin/management.py').read_text(encoding='utf-8')
        self.assertEqual(source.count('@router.'), 10)
        self.assertIn("'/commercial/groups/{group_id}/deactivate'", source)
        self.assertIn("'/commercial/groups/members/{customer_profile_id}/remove'", source)
        self.assertNotIn('models.Model', source)

    def test_read_auth_and_mutation_guards(self):
        admin = (ROOT / 'cow/services/admin_directory.py').read_text(encoding='utf-8')
        groups = (ROOT / 'cow/services/admin_groups.py').read_text(encoding='utf-8')
        self.assertIn('rbac.require(actor, "users.read")',admin)
        self.assertIn('rbac.require(actor, "drivers.read")',admin)
        self.assertIn('rbac.require(actor, "jobs.read")',admin)
        self.assertIn('_actor(actor, "pricing.manage")', groups)
        self.assertIn('GROUP_IN_USE', groups)
        self.assertIn('old.ends_at = now', groups)

if __name__ == '__main__':
    unittest.main()
