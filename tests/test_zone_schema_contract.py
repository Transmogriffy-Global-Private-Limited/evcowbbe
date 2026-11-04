"""Nondestructive static regression checks; not a substitute for PostgreSQL."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MigrationContractTests(unittest.TestCase):
    def test_migration_parses(self):
        ast.parse((ROOT/'cow/migrations/0008_zone_commercial_registry.py').read_text(encoding='utf-8'))

    def test_migration_has_single_predecessor_and_reversal(self):
        source=(ROOT/'cow/migrations/0008_zone_commercial_registry.py').read_text(encoding='utf-8')
        self.assertIn('("cow", "0007_rbac_pricing_consequential_parity")',source)
        self.assertIn('reverse_sql=REVERSE',source)

    def test_migration_has_all_five_entities(self):
        source=(ROOT/'cow/migrations/0008_zone_commercial_registry.py').read_text(encoding='utf-8')
        for entity in ('ServiceZone','DutyZoneAssignment','ChargerZoneAssignment','ZoneGSTAssignment','ZoneTariffPayload'):
            with self.subTest(entity=entity): self.assertIn(f'name="{entity}"',source)

    def test_db_guards_defined_and_reversed(self):
        source=(ROOT/'cow/migrations/0008_zone_commercial_registry.py').read_text(encoding='utf-8')
        for name in ('cow_zone_identity_guard','cow_zone_assignment_guard','cow_zone_gst_guard','cow_zone_tariff_payload_guard'):
            with self.subTest(name=name):
                self.assertIn(f'CREATE FUNCTION {name}',source)
                self.assertIn(f'DROP FUNCTION IF EXISTS {name}',source)

    def test_no_payment_or_hal_activation(self):
        source=(ROOT/'cow/services/zone_commercial_registry.py').read_text(encoding='utf-8')
        for forbidden in ('RequestStart(', 'StartCharging(', 'wallet.Balance', 'providerOrder'):
            self.assertNotIn(forbidden,source)


if __name__ == '__main__': unittest.main()
