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


if __name__ == "__main__":
    unittest.main()
