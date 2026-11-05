"""Static and pure-function guards: this suite NEVER opens or alters PostgreSQL."""
from __future__ import annotations
import ast
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class SchemaWorkflowSafety(unittest.TestCase):
    def test_single_declared_schema_and_five_zone_entities(self):
        schema=ROOT/'cow/models.py'
        self.assertTrue(schema.is_file())
        self.assertFalse((ROOT/'cow/models_zone.py').exists())
        source=ast.parse(schema.read_text(encoding='utf-8'))
        classes={n.name for n in source.body if isinstance(n,ast.ClassDef)}
        for name in ('ServiceZone','DutyZoneAssignment','ChargerZoneAssignment','ZoneGSTAssignment','ZoneTariffPayload'):
            self.assertIn(name,classes)
        for role in ('users','drivers','admin','common'):
            self.assertFalse((ROOT/'cow'/role/'models.py').exists())
            self.assertFalse((ROOT/'cow'/role/'migrations').exists())

    def test_django_migration_ledger_retains_all_history(self):
        migrations=sorted(x.name for x in (ROOT/'cow/migrations').glob('0*.py'))
        self.assertGreaterEqual(len(migrations),8)
        self.assertEqual(migrations[7],'0008_zone_commercial_registry.py')
        sql=(ROOT/'cow/migrations/0008_zone_commercial_registry.py').read_text()
        self.assertIn('reverse_sql=REVERSE',sql)
        for name in ('cow_zone_identity_guard','cow_zone_assignment_guard','cow_zone_gst_guard','cow_zone_tariff_payload_guard'):
            self.assertIn('CREATE FUNCTION '+name,sql)
            self.assertIn('DROP FUNCTION IF EXISTS '+name,sql)

    def test_disposable_pg_harness_knows_0008(self):
        h=(ROOT/'scripts/run_pg_tests.py').read_text()
        self.assertIn('MIGRATIONS_0008',h)
        self.assertIn('TRIGGERS_0008',h)
        self.assertIn('FUNCTIONS_0008',h)
        self.assertIn('--upgrade-0007-to-0008',h)
        self.assertTrue((ROOT/'tests/integration/test_zone_registry_pg.py').is_file())

    def test_schema_cli_fails_closed_by_default(self):
        f=ROOT/'scripts/db.py'
        src=f.read_text()
        self.assertIn('preflight_environment',src)
        self.assertIn('verify_observed_database',src)
        self.assertIn('makemigrations',src)
        self.assertNotIn('DROP DATABASE',src)
        self.assertNotIn('subprocess.run(["psql"',src)
        self.assertIn('--ack-data-loss',src)

    def test_rehost_still_forbids_unauthorized_schema_change(self):
        operator = (ROOT/'ops/vps2/rehost-evcowbbe').read_text()
        self.assertIn('SCHEMA_MIGRATIONS_CHANGED', operator)
        self.assertIn('schema_identical(', operator)
        self.assertIn('schema_identical(previous,rev)', operator)

    def test_isolated_postgresql_runner_sets_and_restores_django_settings(self):
        source = (ROOT / 'scripts/db_isolated_pg.ps1').read_text(encoding='utf-8')
        self.assertIn("'DJANGO_SETTINGS_MODULE'", source)
        self.assertIn("$env:DJANGO_SETTINGS_MODULE = 'config.settings'", source)
        self.assertIn('$oldEnvPresent', source)
        self.assertIn('ISOLATED_CLUSTER_STOPPED=PASS', source)
        self.assertIn('manage.py migrate auth --noinput', source)
        self.assertLess(source.index('manage.py migrate auth --noinput'),
                        source.index('manage.py migrate cow 0007_'))
        self.assertIn('DJANGO_FRAMEWORK_CATALOG=PASS', source)

    def test_vps_review_is_read_only_and_rejects_rewritten_migration(self):
        path=ROOT/'ops/vps2/schema_review.py'
        spec=importlib.util.spec_from_file_location('cow_vps_schema_review',path)
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            a=root/'prior';b=root/'candidate'
            for s in (a,b):
                (s/'cow/migrations').mkdir(parents=True)
                (s/'cow/models.py').write_text('class Test: pass')
                (s/'cow/migrations/0001_initial.py').write_text('baseline')
            self.assertFalse(mod.plan(a,b)['migration_change_required'])
            (b/'cow/migrations/0002_extra.py').write_text('new')
            self.assertTrue(mod.plan(a,b)['migration_change_required'])
            (b/'cow/migrations/0001_initial.py').write_text('rewritten')
            with self.assertRaises(ValueError):mod.plan(a,b)

if __name__=='__main__':unittest.main()
