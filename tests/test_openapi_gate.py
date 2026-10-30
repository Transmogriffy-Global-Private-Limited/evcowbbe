import unittest

from scripts.compare_openapi import diff, scoped_report


class OpenAPIGate(unittest.TestCase):
    def test_missing_and_mutated_contract_is_blocking(self):
        old = {"paths": {"/api/v1/app/me": {"get": {"responses": {"200": {"description": "ok"}}}}}}
        self.assertEqual(len(diff(old, {"paths": {}})[0]), 1)
        changed = {
            "paths": {"/api/v1/app/me": {"get": {"responses": {"200": {"description": "changed"}}}}}
        }
        self.assertEqual(len(diff(old, changed)[1]), 1)
        self.assertEqual(diff(old, old), ([], [], []))

    def test_scoped_report_distinguishes_missing_from_field_differences(self):
        old = {"paths": {"/scope": {"get": {"responses": {"200": {"description": "ok"}}}}}}
        report = scoped_report(old, {"paths": {}}, {"/scope"})
        self.assertEqual(report["missing"], ["GET /scope"])

    def test_scoped_report_normalizes_equivalent_bearer_scheme_names(self):
        old = {
            "components": {"securitySchemes": {"GoBearer": {"type": "http", "scheme": "bearer"}}},
            "paths": {"/scope": {"get": {"security": [{"GoBearer": []}]}}},
        }
        new = {
            "components": {
                "securitySchemes": {"DjangoBearer": {"type": "http", "scheme": "bearer"}}
            },
            "paths": {"/scope": {"get": {"security": [{"DjangoBearer": []}]}}},
        }
        report = scoped_report(old, new, {"/scope"})
        self.assertEqual(report["behavioral_differences"], [])


if __name__ == "__main__":
    unittest.main()
