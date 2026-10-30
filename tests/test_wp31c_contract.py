"""Source-safe validation of exact Annex-44 permission rules and pricing inputs."""

import ast
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from cow.pricing_contract import Draft, overlap

ROOT = Path(__file__).resolve().parents[1]


def valid_draft(**overrides):
    base = dict(
        policy_key="STANDARD",
        policy_version=1,
        scope={"kind": "GLOBAL", "key": "ALL"},
        currency="INR",
        starts_at="2026-10-31T00:00:00+00:00",
        reason="Approved test proposal",
        rules=[
            dict(
                id="VISIT",
                kind="BASE",
                component="VISIT",
                basis="VISITS",
                tax_class="GST_PENDING",
                rate="16.910001",
                priority=1,
            )
        ],
    )
    base.update(overrides)
    return base


class AdminPolicyContractTests(unittest.TestCase):
    def test_decimal_strings_and_modifiers(self):
        d = Draft.model_validate(valid_draft())
        self.assertEqual(d.rules[0].rate, "16.910001")
        self.assertEqual(d.starts_at.utcoffset(), timedelta(0))
        for bad in (16.91, "1e3", "-1", "0.1234567", "01.0"):
            with self.subTest(rate=bad), self.assertRaises(ValidationError):
                Draft.model_validate(
                    valid_draft(rules=[{**valid_draft()["rules"][0], "rate": bad}])
                )

    def test_rule_graph_and_window_validation(self):
        bad_shapes = (
            {"ends_at": "2026-11-01T00:00:00+00:00", "starts_at": None},
            {"ends_at": "2026-10-30T00:00:00+00:00"},
            {"starts_at": "2026-10-31T00:00:00"},
            {
                "rules": [
                    {
                        "id": "D",
                        "kind": "DISCOUNT_PERCENT",
                        "rate": "20",
                        "priority": 1,
                        "target": "NO_BASE",
                    }
                ]
            },
            {"rules": valid_draft()["rules"] * 2},
            {"currency": "USD"},
            {"scope": {"kind": "GLOBAL", "key": "ALL", "untrusted": 1}},
        )
        for changes in bad_shapes:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Draft.model_validate(valid_draft(**changes))

    def test_explicit_deny_and_flat_role_bundles(self):
        tree = ast.parse((ROOT / "cow/services/rbac.py").read_text())
        statements = [
            n
            for n in tree.body
            if isinstance(n, (ast.Assign, ast.FunctionDef))
            and (
                isinstance(n, ast.FunctionDef)
                and n.name == "permitted"
                or isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id in {"CAPABILITIES", "ROLE_DEFAULTS"}
                    for t in n.targets
                )
            )
        ]
        scope = {"frozenset": frozenset}
        exec(compile(ast.Module(body=statements, type_ignores=[]), "rbac-contract", "exec"), scope)
        permitted = scope["permitted"]
        roles = scope["ROLE_DEFAULTS"]
        caps = set(scope["CAPABILITIES"])
        self.assertEqual(set(roles["FULL_ADMIN"]), caps)
        self.assertFalse(permitted({"FULL_ADMIN"}, {"pricing.manage": "DENY"}, "pricing.manage"))
        self.assertTrue(permitted(set(), {"pricing.manage": "ALLOW"}, "pricing.manage"))
        self.assertFalse(permitted({"READ_ONLY"}, {}, "audit.read"))
        self.assertFalse(permitted({"READ_ONLY"}, {}, "pricing.manage"))
        self.assertTrue(permitted({"FINANCE_COMMERCIAL"}, {}, "pricing.manage"))
        self.assertFalse(permitted({"SUPPORT"}, {}, "pricing.manage"))
        self.assertFalse(permitted({"FULL_ADMIN"}, {}, "charging.manage"))

    def test_unbounded_and_touching_windows(self):
        a = datetime(2026, 10, 30, tzinfo=UTC)
        b = a + timedelta(days=1)
        c = b + timedelta(days=1)
        self.assertTrue(overlap(a, c, b, None))
        self.assertFalse(overlap(a, b, b, c))
        self.assertFalse(overlap(b, c, a, b))
        self.assertTrue(overlap(None, None, a, b))
        self.assertTrue(overlap(a, None, b, c))

    def test_catalog_follows_normative_annex_44(self):
        source = (ROOT / "cow/services/rbac.py").read_text()
        tree = ast.parse(source)
        caps = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "CAPABILITIES" for t in n.targets)
        )
        values = ast.literal_eval(caps.value)
        self.assertEqual(len(values), 45)
        self.assertEqual(len(values), len(set(values)))
        self.assertNotIn("charging.manage", values)
        self.assertNotIn("finance.manage", values)
        for name in ("drivers.review", "charging.command", "pricing.manage", "audit.read"):
            self.assertIn(name, values)
        self.assertIn('"READ_ONLY": frozenset', source)
        self.assertNotIn('"audit.read",\n    }),\n}', source)


if __name__ == "__main__":
    unittest.main()
