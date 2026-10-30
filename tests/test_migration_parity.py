"""Static source/migration consistency, NOT a PostgreSQL catalog equivalence test."""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class MigrationParity(unittest.TestCase):
    def test_migration_lineage_model_and_field_coverage(self):
        src = ast.parse((ROOT / "cow/models.py").read_text())
        model_fields = {}
        inherited = []
        for node in src.body:
            if isinstance(node, ast.ClassDef) and node.name == "Timestamped":
                inherited = [s.targets[0].id for s in node.body if isinstance(s, ast.Assign)]
        for node in src.body:
            if not isinstance(node, ast.ClassDef) or node.name == "Timestamped":
                continue
            if not any(
                isinstance(x, ast.Name)
                and x.id == "Timestamped"
                or isinstance(x, ast.Attribute)
                and x.attr == "Model"
                for x in node.bases
            ):
                continue
            own = [s.targets[0].id for s in node.body if isinstance(s, ast.Assign)]
            model_fields[node.name] = set(
                own
                + (
                    inherited
                    if any(isinstance(x, ast.Name) and x.id == "Timestamped" for x in node.bases)
                    else []
                )
            )
        migrated = {}
        for path in (ROOT / "cow/migrations").glob("0*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr == "CreateModel":
                    name = next(a.value.value for a in node.keywords if a.arg == "name")
                    fieldlist = next(a.value for a in node.keywords if a.arg == "fields")
                    migrated[name.lower()] = set(t.elts[0].value for t in fieldlist.elts)
                elif node.func.attr == "AddField":
                    name = next(a.value.value for a in node.keywords if a.arg == "model_name")
                    field = next(a.value.value for a in node.keywords if a.arg == "name")
                    migrated.setdefault(name, set()).add(field)
        self.assertEqual({name.lower(): fields for name, fields in model_fields.items()}, migrated)

    def test_sql_guard_tables_exist(self):
        s = (ROOT / "cow/migrations/0002_business_invariants.py").read_text()
        migration = (ROOT / "cow/migrations/0001_initial.py").read_text()
        names = re.findall(r"name='(\w+)'", migration)
        generated_tables = {"cow_" + name.lower() for name in names}
        references = set(re.findall(r"\b(?:ON|FROM)\s+(cow_\w+)", s))
        self.assertFalse(references - generated_tables, references - generated_tables)

    def test_0004_reconciles_legacy_pairings_before_lifecycle_constraints(self):
        path = ROOT / "cow/migrations/0004_admin_charger_connector_pairing_parity.py"
        migration = path.read_text()
        self.assertIn(
            'effective_to__isnull=False, effective_to__lte=F("effective_from")', migration
        )
        self.assertIn('update(status="ENDED")', migration)
        self.assertIn(
            "field=models.DateTimeField(auto_now_add=True, blank=True, null=True)", migration
        )
        self.assertIn("field=models.CharField(blank=True, max_length=500, null=True)", migration)
        self.assertLess(
            migration.index("migrations.RunPython(reconcile_legacy_pairings"),
            migration.index('name="cow_pairing_lifecycle"'),
        )

    def test_0004_historical_created_at_is_not_backfilled(self):
        path = ROOT / "cow/migrations/0004_admin_charger_connector_pairing_parity.py"
        migration = path.read_text()
        add = (
            "migrations.AddField(\n"
            '            model_name="vehiclechargerpairing",\n'
            '            name="created_at",\n'
            "            field=models.DateTimeField(blank=True, null=True),"
        )
        change = (
            "migrations.AlterField(\n"
            '            model_name="vehiclechargerpairing",\n'
            '            name="created_at",\n'
            "            field=models.DateTimeField(auto_now_add=True, blank=True, null=True),"
        )
        reconcile = "migrations.RunPython(reconcile_legacy_pairings, migrations.RunPython.noop)"
        self.assertEqual(migration.count(add), 1)
        self.assertEqual(migration.count(change), 1)
        self.assertLess(migration.index(add), migration.index(reconcile))
        self.assertLess(migration.index(reconcile), migration.index(change))
        self.assertLess(migration.index(change), migration.index('name="cow_pairing_lifecycle"'))

    def test_reset_guard_matches_migration_tables_and_refuses_fatally(self):
        reset = (ROOT / "scripts/reset_pg_acceptance_schema.sql").read_text()
        pattern = r"AND c\.relname NOT IN \((.*?)\)\s*\) AS public_objects_allowed"
        match = re.search(pattern, reset, re.S)
        self.assertIsNotNone(match)
        allowed = set(re.findall(r"'(cow_[a-z0-9_]+)'", match.group(1)))
        generated = set()
        for name in (
            "0001_initial.py",
            "0003_admin_fleet_vehicle_parity.py",
            "0004_admin_charger_connector_pairing_parity.py",
            "0006_customer_master_parity.py",
        ):
            migration = ast.parse((ROOT / "cow/migrations" / name).read_text())
            for node in ast.walk(migration):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Attribute) or node.func.attr != "CreateModel":
                    continue
                name_node = next(x.value for x in node.keywords if x.arg == "name")
                generated.add("cow_" + name_node.value.lower())
        self.assertEqual(allowed, generated)
        self.assertIn("array_agg(name::text ORDER BY name)", reset)
        self.assertNotIn("array_agg(name ORDER BY name)", reset)
        self.assertNotIn("count(*) IN (3, 4)", reset)
        self.assertEqual(
            reset.count(r"\else"),
            reset.count("RAISE EXCEPTION 'COW_TEST_RESET_REFUSED'"),
        )
        self.assertNotIn(r"\quit", reset)
        self.assertIn("public_extensions_absent", reset)
        self.assertIn("DROP SCHEMA public CASCADE;", reset)


if __name__ == "__main__":
    unittest.main()
