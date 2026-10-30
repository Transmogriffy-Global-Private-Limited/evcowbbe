import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class SourceIntegrity(unittest.TestCase):
    def test_all_python_sources_parse(self):
        for path in [ROOT / "manage.py"] + [
            p
            for folder in ("cow", "config", "scripts", "tests")
            for p in (ROOT / folder).rglob("*.py")
        ]:
            with self.subTest(path=path.relative_to(ROOT)):
                ast.parse(path.read_text())

    def test_migration_source_is_not_runtime_auto_create(self):
        for path in (ROOT / "cow").rglob("*.py"):
            if "migrations" in path.parts:
                continue
            t = path.read_text()
            self.assertNotIn("schema_editor.create_model", t)
            self.assertNotIn("call_command('migrate'", t)

    def test_schema_has_single_django_migration_lineage(self):
        migrations = sorted((ROOT / "cow/migrations").glob("0*.py"))
        self.assertEqual(
            [p.name for p in migrations],
            [
                "0001_initial.py",
                "0002_business_invariants.py",
                "0003_admin_fleet_vehicle_parity.py",
                "0004_admin_charger_connector_pairing_parity.py",
                "0005_admin_rbac_pricing_parity.py",
                "0006_customer_master_parity.py",
            ],
        )
        for p in migrations:
            self.assertIn("migrations.", p.read_text())


if __name__ == "__main__":
    unittest.main()
