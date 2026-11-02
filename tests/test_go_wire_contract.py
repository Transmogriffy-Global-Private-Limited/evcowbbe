"""Frozen Go external-wire regression tests, no live database or listener.

Run in the Python checkout after applying the installer. DB-backed operation
behavior remains a separate acceptance gate, not replaced by this unit suite.
"""
import unittest
from uuid import uuid4

import yaml

from cow import go_wire


class GoContractCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(go_wire.SPEC_PATH.read_text(encoding="utf-8"))

    def test_all_82_method_paths_are_recognized(self):
        count = 0
        for path, entry in self.doc["paths"].items():
            concrete = path
            for variable in __import__("re").findall(r"\{(.*?)\}", path):
                concrete = concrete.replace("{" + variable + "}", str(uuid4()))
            for method in ("get", "post", "patch", "put", "delete"):
                if method in entry:
                    count += 1
                    self.assertIsNotNone(go_wire.match_operation(concrete, method), (method, path))
            self.assertTrue(go_wire.is_go_path(concrete), path)
        self.assertEqual(count, 82)

    def test_python_only_routes_are_not_rewritten(self):
        for path in (
            "/api/v1/app/vehicles", "/api/v1/app/addresses",
            "/api/v1/admin/jobs", "/api/v1/internal/hal/facts",
        ):
            self.assertIsNone(go_wire.match_operation(path, "POST"), path)
            payload = {"data": {"status": "python-only", "detail": "preserve"}}
            self.assertEqual(go_wire.success_body(path, "POST", 200, payload), payload)

    def test_go_success_envelopes(self):
        account = str(uuid4())
        cases = [
            ("/api/v1/auth/verify-email", "POST", {"message": "Email verified"}, {"ok": True}),
            ("/api/v1/auth/reset-password", "POST", {"message": "Reset"}, {"ok": True}),
            ("/api/v1/auth/forgot-password", "POST", {"message": "Queued"}, {"accepted": True}),
            ("/api/v1/auth/resend-verification", "POST", {"message": "Queued"}, {"accepted": True}),
            ("/api/v1/admin/auth/logout", "POST", {"message": "OK"}, {"ok": True}),
            ("/api/v1/app/auth/password/change", "POST", {"message": "OK"}, {"ok": True}),
            ("/api/v1/driver/auth/2fa/enable/confirm", "POST", {"two_factor_enabled": True}, {"ok": True}),
            ("/api/v1/admin/rbac/accounts/" + account + "/roles", "POST", {"roles": ["READ_ONLY"]}, {"ok": True}),
        ]
        for path, method, actual, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(go_wire.success_body(path, method, 200, {"data": actual}), {"data": expected})

    def test_go_profile(self):
        account = str(uuid4())
        payload = {"data": {
            "account_id": account, "principal_id": str(uuid4()),
            "status": "PENDING", "email": "driver@example.test", "plane": "driver",
            "email_verified": True, "two_factor_enabled": False,
            "profile": {"python_extension": 1},
        }}
        normalized = go_wire.success_body("/api/v1/driver/me", "GET", 200, payload)
        self.assertEqual(normalized, {"data": {
            "id": account, "email": "driver@example.test", "plane": "driver",
            "principal_status": "PENDING", "email_verified": True, "two_factor_enabled": False,
        }})

    def test_token_flattening_and_errors_untouched(self):
        p = "/api/v1/app/auth/refresh"
        tokens = {"access_token": "redacted", "refresh_token": "redacted"}
        source = {"data": {"two_factor_required": False, "tokens": tokens}}
        self.assertEqual(go_wire.success_body(p, "POST", 200, source), {"data": tokens})
        self.assertEqual(go_wire.success_body(p, "POST", 401, {"error": {"code": "UNAUTHORIZED"}}),
                         {"error": {"code": "UNAUTHORIZED"}})

    def test_go_json_body_32k_and_media_validation(self):
        p = "/api/v1/app/auth/login"
        self.assertEqual(go_wire.check_request(p, "POST", "text/plain", b"{}", has_request_body=True)[0], 415)
        self.assertIsNone(go_wire.check_request(p, "POST", "application/json", b'{}', has_request_body=True))
        self.assertEqual(go_wire.check_request(p, "POST", "application/json", b'x' * 32769, has_request_body=True)[0], 400)
        for payload in (b'', b'{', b'{}{}', b'NaN', b'{"a":NaN}', b'[]'):
            with self.subTest(payload=payload):
                self.assertEqual(go_wire.check_request(p, "POST", "application/json", payload, has_request_body=True)[0], 400)
        # Go's generic JSON decoder doesn't disallow duplicate object keys.
        self.assertIsNone(go_wire.check_request(p, "POST", "application/json", b'{"x":1,"x":2}', has_request_body=True))

    def test_go_cors_exact_allowlist(self):
        self.assertIsNone(go_wire.check_preflight("http://localhost:5173", "POST", "authorization,content-type"))
        self.assertEqual(go_wire.check_preflight("http://evil.test", "POST", "authorization")[0], 403)
        self.assertEqual(go_wire.check_preflight("http://localhost:5173", "TRACE", "authorization")[0], 400)
        self.assertEqual(go_wire.check_preflight("http://localhost:5173", "POST", "proxy-authorization")[0], 400)


if __name__ == "__main__":
    unittest.main()
