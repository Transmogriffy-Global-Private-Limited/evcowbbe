"""FE documentation invariants: live JSON/YAML symmetry and source-derived contracts.

No database connections or mutations. The runtime endpoint is never invoked as a
charging/payment operation; only HTTP GET on documentation and health docs.
"""

from __future__ import annotations

import json
import os
import unittest
from copy import deepcopy

import yaml

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


class FrontendOpenAPIContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import django
        from django.test import Client

        django.setup()
        cls.client = Client()

    def test_json_yaml_are_the_same_document(self):
        as_json = self.client.get("/api/openapi.json")
        as_yaml = self.client.get("/openapi.yaml")
        self.assertEqual(as_json.status_code, 200)
        self.assertEqual(as_yaml.status_code, 200)
        self.assertEqual(as_json.json(), yaml.safe_load(as_yaml.content))
        self.assertIn("/health/ready", as_json.json()["paths"])
        self.assertIn("/version", as_json.json()["paths"])

    def test_every_operation_is_explained_and_grouped(self):
        document = self.client.get("/api/openapi.json").json()
        groups = {item["name"] for item in document["tags"]}
        self.assertGreater(len(groups), 8)
        self.assertIn("Frontend integration guide", document["info"]["description"])
        self.assertEqual(document["servers"], [{
            "url": "/", "description": "Same-origin development API"
        }])
        operations = 0
        for path, resource in document["paths"].items():
            for method, operation in resource.items():
                if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                with self.subTest(endpoint=f"{method.upper()} {path}"):
                    operations += 1
                    self.assertTrue(operation.get("operationId"))
                    self.assertTrue(operation.get("summary"))
                    self.assertTrue(operation.get("description"))
                    self.assertEqual(len(operation["tags"]), 1)
                    self.assertIn(operation["tags"][0], groups)
                    self.assertIn(operation["x-fe-schema-detail"], {
                        "declared", "generic-success"
                    })
                    if operation["x-fe-schema-detail"] == "generic-success":
                        self.assertIn("Documentation gap", operation["description"])
        self.assertGreaterEqual(operations, 65)

    def test_customer_create_and_mutation_contracts(self):
        document = self.client.get("/api/openapi.json").json()
        paths = document["paths"]
        expected = {
            ("post", "/api/v1/app/vehicles"): ("201", "FE_CustomerVehicleCreate"),
            ("post", "/api/v1/app/addresses"): ("201", "FE_CustomerAddressCreate"),
            ("patch", "/api/v1/app/profile"): ("200", "FE_CustomerProfilePatch"),
            ("patch", "/api/v1/app/vehicles/{vehicle_id}"): (
                "200", "FE_CustomerVehiclePatch"
            ),
            ("patch", "/api/v1/app/addresses/{address_id}"): (
                "200", "FE_CustomerAddressPatch"
            ),
        }
        for (method, path), (status, body_type) in expected.items():
            with self.subTest(endpoint=f"{method.upper()} {path}"):
                operation = paths[path][method]
                self.assertIn(status, operation["responses"])
                self.assertEqual(
                    operation["requestBody"]["content"]["application/json"]["schema"],
                    {"$ref": "#/components/schemas/" + body_type},
                )
                header_params = {
                    x["name"]: x for x in operation["parameters"]
                    if x.get("in") == "header"
                }
                self.assertIn("Idempotency-Key", header_params)
                self.assertEqual(header_params["Idempotency-Key"]["required"], method == "patch")
                if method == "patch":
                    self.assertTrue(header_params["If-Match"]["required"])
                    self.assertIn("412", operation["responses"])
                    self.assertIn("428", operation["responses"])
        for path in ("/api/v1/app/vehicles", "/api/v1/app/addresses"):
            self.assertNotIn("200", paths[path]["post"]["responses"])
        for name in (
            "FE_CustomerProfileEnvelope", "FE_CustomerVehicleEnvelope",
            "FE_CustomerAddressEnvelope", "FE_CustomerVehicleListEnvelope",
            "FE_CustomerAddressListEnvelope",
        ):
            self.assertIn(name, document["components"]["schemas"])

    def test_existing_contract_identifiers_security_and_noncustomer_bodies_survive(self):
        from ninja import NinjaAPI

        from cow.api import api
        from cow.openapi import add_diagnostics

        raw = json.loads(json.dumps(add_diagnostics(NinjaAPI.get_openapi_schema(api))))
        decorated = self.client.get("/api/openapi.json").json()
        self.assertEqual(set(decorated["paths"]), set(raw["paths"]))
        for path, item in raw["paths"].items():
            for method, old in item.items():
                if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                new = decorated["paths"][path][method]
                with self.subTest(endpoint=f"{method.upper()} {path}"):
                    self.assertEqual(new.get("operationId"), old.get("operationId"))
                    self.assertEqual(new.get("security"), old.get("security"))
                    if not path.startswith("/api/v1/app/profile") and not path.startswith(
                        ("/api/v1/app/vehicles", "/api/v1/app/addresses")
                    ):
                        old_body = deepcopy(old.get("requestBody"))
                        new_body = deepcopy(new.get("requestBody"))
                        # Only documented examples may be added for plane login.
                        if isinstance(new_body, dict):
                            for medium in new_body.get("content", {}).values():
                                if isinstance(medium, dict):
                                    medium.pop("examples", None)
                        self.assertEqual(new_body, old_body)
                        # Frontend documentation may ADD known Zone/Commercial
                        # error responses; it must never remove/rewrite the
                        # raw Ninja contract or invent arbitrary statuses.
                        original = old.get("responses", {})
                        enriched = new.get("responses", {})
                        permitted = set()
                        op_id = old.get("operationId")
                        zone = path.startswith("/api/v1/admin/zones")
                        commercial = path.startswith("/api/v1/admin/commercial/")
                        if zone or commercial:
                            permitted = {"401", "403", "404", "409", "422"}
                            is_write = method == "post" and path not in {
                                "/api/v1/admin/zones/preview",
                                "/api/v1/admin/commercial/preview",
                            }
                            if is_write:
                                permitted.update({"400", "415", "428"})
                            if (zone and op_id in {
                                "adminZoneTariffPublish", "adminZoneTariffWithdraw"
                            }) or (commercial and op_id == "adminCommercialTariffPublish"):
                                permitted.add("412")
                        self.assertEqual(
                            set(enriched) - set(original), permitted - set(original)
                        )
                        for status, previous in original.items():
                            current = deepcopy(enriched[status])
                            if (zone and str(status).startswith("2") and op_id in {
                                "adminZoneTariffDraft", "adminZoneTariffGet",
                                "adminZoneTariffPublish", "adminZoneTariffWithdraw",
                            } and "ETag" not in previous.get("headers", {})):
                                self.assertIn("ETag", current.get("headers", {}))
                                self.assertIn("pattern", current["headers"]["ETag"]["schema"])
                                del current["headers"]["ETag"]
                                if not current["headers"]:
                                    del current["headers"]
                            self.assertEqual(current, previous)
                        if permitted:
                            from cow.common.contracts import GO_ERROR_ENVELOPE_SCHEMA
                            for status in permitted - set(original):
                                entry = enriched[status]
                                self.assertTrue(entry.get("description"))
                                self.assertEqual(
                                    entry["content"]["application/json"]["schema"],
                                    GO_ERROR_ENVELOPE_SCHEMA,
                                )

    def test_hal_202_is_never_documented_as_physical_start(self):
        paths = self.client.get("/api/openapi.json").json()["paths"]
        op = paths["/api/v1/admin/jobs/{job_id}/charging/start"]["post"]
        self.assertIn("not", op["description"].lower())
        self.assertIn("physical", op["description"].lower())
        fact = paths["/api/v1/internal/hal/facts"]["post"]
        self.assertIn("server-to-server", fact["description"])


if __name__ == "__main__":
    unittest.main()
