"""Source and strict-Pydantic WP3.1D checks, no database dependency."""

import unittest
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from cow.customer_contract import (
    AddressCreate,
    AddressPatch,
    ProfilePatch,
    VehicleCreate,
    VehiclePatch,
    merged_address,
    merged_vehicle,
)

ROOT = Path(__file__).parents[1]


def valid_ev():
    return {"make": "Example", "model": "EV", "supports_ac": True, "supports_dc": False}


def valid_address():
    return {"address": "21 Lake Road", "latitude": "22.9475572", "longitude": "88.4614943"}


class CustomerContractTests(unittest.TestCase):
    def assert_invalid(self, model, data):
        with self.assertRaises(ValidationError):
            model.model_validate(data)

    def test_no_implicit_charging_support(self):
        self.assert_invalid(VehicleCreate, {"make": "Example", "model": "EV"})
        self.assert_invalid(VehicleCreate, {**valid_ev(), "supports_ac": False})
        self.assert_invalid(VehicleCreate, {**valid_ev(), "max_dc_power_w": 30000})
        v = VehicleCreate.model_validate(valid_ev())
        self.assertTrue(v.supports_ac)
        self.assertFalse(v.supports_dc)

    def test_vehicle_validation_and_merge(self):
        self.assert_invalid(VehicleCreate, {**valid_ev(), "battery_capacity_wh": -1})
        self.assert_invalid(VehicleCreate, {**valid_ev(), "connector_types": ["ccs2", " CCS2 "]})
        self.assert_invalid(VehicleCreate, {**valid_ev(), "admin": True})
        self.assert_invalid(VehiclePatch, {})
        self.assert_invalid(VehiclePatch, {"make": None})
        self.assert_invalid(VehiclePatch, {"active": False})
        old = VehicleCreate.model_validate(valid_ev())
        updated = merged_vehicle(old.model_dump(), VehiclePatch(label="Family EV", variant="GT"))
        self.assertEqual(updated.label, "Family EV")
        self.assertEqual(updated.variant, "GT")
        self.assertTrue(updated.supports_ac)
        self.assert_invalid(VehiclePatch, {"supports_ac": "true"})

    def test_address_validation_and_merge(self):
        address = AddressCreate.model_validate(valid_address())
        self.assertEqual(address.latitude, Decimal("22.9475572"))
        self.assert_invalid(AddressCreate, {**valid_address(), "latitude": "90.0000001"})
        self.assert_invalid(AddressCreate, {**valid_address(), "longitude": "88.12345678"})
        self.assert_invalid(AddressCreate, {**valid_address(), "country_code": "in"})
        self.assert_invalid(AddressCreate, {**valid_address(), "is_default": True})
        self.assert_invalid(AddressPatch, {})
        self.assert_invalid(AddressPatch, {"city": None})
        updated = merged_address(address.model_dump(), AddressPatch(city="Kolkata"))
        self.assertEqual(updated.city, "Kolkata")
        self.assertEqual(updated.country_code, "IN")

    def test_profile_allowlist(self):
        self.assert_invalid(ProfilePatch, {})
        self.assert_invalid(ProfilePatch, {"email": "another@example.com"})
        self.assert_invalid(ProfilePatch, {"status": "ACTIVE"})
        self.assert_invalid(ProfilePatch, {"full_name": None})
        self.assert_invalid(ProfilePatch, {"phone": "123456"})
        self.assertEqual(ProfilePatch.model_validate({"phone": "+919876543210"}).phone, "+919876543210")

    def test_twelve_routes_are_defined_and_immutable_snapshots_not_rewritten(self):
        src = (ROOT / "cow/customer_api.py").read_text()
        expected = (
            '"/profile"', '"/vehicles"', '"/vehicles/{vehicle_id}"',
            '"/addresses"', '"/addresses/{address_id}"',
            '"/addresses/{address_id}/make-default"',
        )
        for route in expected:
            self.assertIn(route, src)
        self.assertEqual(src.count("@router."), 12)
        self.assertNotIn("ServiceRequest.objects.update", src)
        self.assertNotIn("ServiceJob.objects.update", src)
        self.assertNotIn("Quote.objects.update", src)
        mig = (ROOT / "cow/migrations/0006_customer_master_parity.py").read_text()
        self.assertIn('"0005_admin_rbac_pricing_parity"', mig)
        self.assertIn("cow_customer_profile_guard", mig)
        self.assertIn("cow_customer_address_guard", mig)
        self.assertIn("cow_customer_vehicle_guard", mig)


if __name__ == "__main__":
    unittest.main()
