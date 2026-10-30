"""Source-level contract checks that do not claim PostgreSQL acceptance."""

import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import yaml

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


class FleetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import django

        django.setup()

    def test_diagnostics_and_all_nine_vehicle_operations_are_generated(self):
        from django.test import Client

        generated = Client().get("/openapi.yaml")
        self.assertEqual(generated.status_code, 200)
        self.assertEqual(generated["Content-Type"], "application/yaml")
        paths = yaml.safe_load(generated.content)["paths"]
        expected = {
            ("/health/live", "get"),
            ("/health/ready", "get"),
            ("/version", "get"),
            ("/api/v1/admin/vehicles", "get"),
            ("/api/v1/admin/vehicles", "post"),
            ("/api/v1/admin/vehicles/{vehicle_id}", "get"),
            ("/api/v1/admin/vehicles/{vehicle_id}", "patch"),
            ("/api/v1/admin/vehicles/{vehicle_id}/history", "get"),
            ("/api/v1/admin/vehicles/{vehicle_id}/availability", "get"),
            ("/api/v1/admin/vehicles/{vehicle_id}/deactivate", "post"),
            ("/api/v1/admin/vehicles/{vehicle_id}/reactivate", "post"),
            ("/api/v1/admin/vehicles/{vehicle_id}/maintenance", "post"),
        }
        self.assertTrue(all(method in paths[path] for path, method in expected))
        self.assertEqual(
            yaml.safe_load(generated.content)["components"]["securitySchemes"]["AdminFleetBearer"],
            {"type": "http", "scheme": "bearer", "bearerFormat": "opaque"},
        )

    def test_scoped_openapi_report_has_no_missing_or_behavioral_difference(self):
        from django.test import Client

        from scripts.compare_openapi import scoped_report

        expected_paths = {
            "/health/live",
            "/health/ready",
            "/version",
            "/api/v1/admin/vehicles",
            "/api/v1/admin/vehicles/{vehicle_id}",
            "/api/v1/admin/vehicles/{vehicle_id}/history",
            "/api/v1/admin/vehicles/{vehicle_id}/availability",
            "/api/v1/admin/vehicles/{vehicle_id}/deactivate",
            "/api/v1/admin/vehicles/{vehicle_id}/reactivate",
            "/api/v1/admin/vehicles/{vehicle_id}/maintenance",
        }
        baseline = yaml.safe_load(
            (Path(__file__).parents[1] / "docs/contracts/auth.openapi.yaml").read_text()
        )
        generated = yaml.safe_load(Client().get("/openapi.yaml").content)
        report = scoped_report(baseline, generated, expected_paths)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["behavioral_differences"], [])
        self.assertGreater(len(report["representation_differences"]), 0)

    def test_diagnostics_have_public_envelopes_security_headers_and_bounded_failure(self):
        from django.test import Client

        client = Client()
        live = client.get("/health/live")
        version = client.get("/version")
        self.assertEqual(live.json(), {"data": {"status": "live"}})
        self.assertEqual(version.json(), {"data": {"version": "admin-pricing-policy-1"}})
        self.assertEqual(live["Cache-Control"], "no-store")
        self.assertIn("X-Request-ID", live)
        with (
            patch("config.urls.transaction.atomic", return_value=nullcontext()),
            patch(
                "config.urls.connection.cursor",
                side_effect=RuntimeError("private connection string"),
            ),
        ):
            unavailable = client.get("/health/ready")
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["error"]["code"], "DEPENDENCY_UNAVAILABLE")
        self.assertNotIn("private connection string", unavailable.content.decode())

    def test_vehicle_schemas_reject_unknown_invalid_and_empty_patch(self):
        from pydantic import ValidationError

        from cow.fleet_api import FleetVehicleCreate, FleetVehiclePatch

        with self.assertRaises(ValidationError):
            FleetVehicleCreate(asset_tag="bad tag!", make="A", model="B")
        with self.assertRaises(ValidationError):
            FleetVehicleCreate(asset_tag="AB", make="A", model="B", unknown="no")
        self.assertEqual(FleetVehiclePatch().model_dump(exclude_unset=True), {})

    def test_new_migration_is_additive_and_has_overlap_and_immutability_guards(self):
        text = (
            Path(__file__).parents[1] / "cow/migrations/0003_admin_fleet_vehicle_parity.py"
        ).read_text()
        self.assertIn("0002_business_invariants", text)
        self.assertIn("cow_vehicle_maintenance_no_overlap", text)
        self.assertIn("cow_vehicle_maintenance_immutable", text)
        self.assertIn("FOR UPDATE", text)
        self.assertIn("23P01", text)
