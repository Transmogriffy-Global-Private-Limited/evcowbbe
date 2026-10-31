"""Pure policy behavior and static coverage of critical entrypoints."""
import ast
from pathlib import Path
import unittest

import django
django.setup()

from cow.services.auth import _auth_status_allowed

class DriverAuthenticationParity(unittest.TestCase):
    def test_eligible_status_and_plane_matrix(self):
        for plane in ("app", "driver", "admin", "unknown"):
            for status in ("PENDING", "ACTIVE", "SUSPENDED", "UNKNOWN", ""):
                should = status == "ACTIVE" and plane in ("app", "driver", "admin") or (plane == "driver" and status == "PENDING")
                with self.subTest(plane=plane, status=status):
                    self.assertEqual(_auth_status_allowed(plane, status), should)

    def test_authorization_is_used_by_all_token_issuance_and_check_paths(self):
        text = (Path(__file__).parents[1] / "cow/services/auth.py").read_text(encoding="utf-8")
        tree = ast.parse(text)
        for method in ("login", "authenticate", "refresh", "verify_login_otp"):
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == method)
            self.assertIn("_auth_status_allowed(", ast.get_source_segment(text, node), method)
        self.assertIn("absolute_expires_at__gt=timezone.now()", text)
        self.assertIn("principal.account.two_factor_enabled", text)

if __name__ == "__main__":
    unittest.main()
