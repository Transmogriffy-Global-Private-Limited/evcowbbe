"""Offline integrity checks for the Stage B shared admission integration."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OperationalAdmissionSource(unittest.TestCase):
    def read(self, path):
        source = (ROOT / path).read_text(encoding="utf-8")
        ast.parse(source, filename=path)
        return source

    def test_shared_service_and_hardware_boundaries(self):
        source = self.read("cow/services/operational_eligibility.py")
        for phrase in (
            "def lock_gate()", "def fresh_admin(", "def assert_start_admissible(",
            "ACTIVE_CHARGING_SESSION", "UNRESOLVED_HAL_COMMAND", "DUTY_NOT_ACTIVE",
            "DRIVER_NOT_ELIGIBLE", "CONNECTOR_NOT_OPERATIONAL",
            "ASSET_PAIRING_MISMATCH_BLOCKED", "CHARGING_COMMAND_CONFLICT",
        ):
            self.assertIn(phrase, source)
        self.assertNotIn("httpx", source)
        self.assertNotIn("HALCommand.objects.create", source)

    def test_cross_code_paths_inherit_the_gate(self):
        fleet = self.read("cow/services/fleet.py")
        self.assertEqual(fleet.count("admission.fresh_admin(actor,"), 12)
        self.assertEqual(fleet.count("admission.lock_gate()"), 12)
        # The physical safety check must precede the pairing-only conflict.
        # Both deactivations must preserve the legacy pairing-only rejection.
        tree = ast.parse(fleet)
        for method, owner in (("set_active", "vehicle"), ("set_charger_active", "charger")):
            functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name == method]
            self.assertEqual(len(functions), 1)
            branches = [node for node in ast.walk(functions[0])
                        if isinstance(node, ast.If)
                        and isinstance(node.test, ast.UnaryOp)
                        and isinstance(node.test.op, ast.Not)
                        and isinstance(node.test.operand, ast.Name)
                        and node.test.operand.id == "active"]
            self.assertEqual(len(branches), 1)
            body = branches[0].body
            self.assertGreaterEqual(len(body), 2)
            self.assertIsInstance(body[0], ast.Expr)
            guard = body[0].value
            self.assertIsInstance(guard, ast.Call)
            self.assertEqual(ast.unparse(guard.func), "admission.ensure_quiescent")
            self.assertEqual(len(guard.args), 0)
            self.assertEqual([item.arg for item in guard.keywords], [owner + "_id"])
            self.assertEqual(ast.unparse(guard.keywords[0].value), owner + ".pk")
            rest = ast.unparse(ast.Module(body=body[1:], type_ignores=[]))
            self.assertIn("VehicleChargerPairing.objects.filter", rest)
            self.assertIn("CONFLICT", rest)
        self.assertIn("admission.ensure_quiescent(vehicle_id=pairing.vehicle_id, charger_id=pairing.charger_id)", fleet)
        jobs = self.read("cow/admin/jobs.py")
        self.assertIn("admission.assert_start_admissible(", jobs)
        self.assertIn('admission.fresh_admin(request.auth, "charging.command")', jobs)
        client = self.read("cow/hal/client.py")
        self.assertIn("command.state = \"REJECTED\"", client)
        self.assertIn("admission.assert_start_admissible(", client)
        ingress = self.read("cow/hal/ingress.py")
        self.assertIn("admission.lock_gate()", ingress)
        fe = self.read("cow/fe_openapi.py")
        self.assertIn("an open SERVICING duty", fe)
        self.assertIn('"Idempotency-Key", "required": True', fe)
        self.assertIn("ChargingCommandEnvelope", jobs)

    def test_existing_routes_and_migrations_unchanged(self):
        self.assertEqual(self.read("cow/admin/jobs.py").count("@router."), 3)
        self.assertFalse((ROOT / "cow/migrations/0011_operational_eligibility.py").exists())


if __name__ == "__main__":
    unittest.main()
