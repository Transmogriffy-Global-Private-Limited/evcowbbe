"""In-process HTTP evidence for Go-wire middleware behavior without a database."""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


class GoWireHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import django

        django.setup()
        from django.test import Client

        cls.client = Client()

    def test_diagnostic_wire_responses_and_standard_headers(self):
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"data": {"status": "live"}})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("X-Request-ID", response)
        self.assertEqual(self.client.get("/version").json(), {"data": {"version": "admin-pricing-policy-1"}})

    def test_unavailable_readiness_uses_the_shared_error_envelope(self):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "DEPENDENCY_UNAVAILABLE")
        self.assertEqual(response.json()["error"]["details"], {})

    def test_public_go_json_media_and_size_gate(self):
        response = self.client.post(
            "/api/v1/auth/verify-email", data=b"{}", content_type="text/plain"
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")
        response = self.client.post(
            "/api/v1/auth/verify-email",
            data=b"x" * 32769,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_FAILED")

    def test_go_omitted_action_token_reaches_the_domain_error(self):
        # Go's encoding/json decodes a missing onlyToken.Token as ""; its
        # service rejects that value as invalid credentials.  This must not
        # become Ninja's generic request-schema 400 response.
        response = self.client.post(
            "/api/v1/auth/verify-email", data=b"{}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_go_token_and_utf8_password_boundaries(self):
        from cow.errors import DomainError
        from cow.services.auth import validate_opaque_token, validate_password

        validate_opaque_token("A" * 43)
        validate_password("e" * 12)
        validate_password("é" * 128)  # 256 UTF-8 bytes, not 128 characters.
        # Go validates the fixed byte length before it hashes a presented
        # credential; it does not reject a non-URL-safe 43-byte input at the
        # request boundary. Issued credentials are URL-safe separately.
        validate_opaque_token("!" * 43)
        with self.assertRaises(DomainError):
            validate_opaque_token("A" * 42)
        with self.assertRaises(DomainError):
            validate_password("é" * 129)

    def test_go_cors_preflight_and_rejection(self):
        headers = {
            "HTTP_ORIGIN": "http://localhost:5173",
            "HTTP_ACCESS_CONTROL_REQUEST_METHOD": "POST",
            "HTTP_ACCESS_CONTROL_REQUEST_HEADERS": "authorization,content-type",
        }
        response = self.client.options("/api/v1/auth/verify-email", **headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertEqual(response["Access-Control-Allow-Methods"], "GET, HEAD, POST, PUT, PATCH, DELETE")
        response = self.client.options(
            "/api/v1/auth/verify-email",
            HTTP_ORIGIN="https://untrusted.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "CORS_ORIGIN_FORBIDDEN")

    def test_go_method_mismatch_has_the_standard_error_envelope(self):
        response = self.client.get("/api/v1/auth/verify-email")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "METHOD_NOT_ALLOWED")
        self.assertIn("request_id", response.json()["error"])

    def test_go_bearer_failures_use_the_pinned_invalid_credentials_contract(self):
        for path, headers in (
            ("/api/v1/app/me", {}),
            ("/api/v1/driver/me", {"HTTP_AUTHORIZATION": "Bearer invalid"}),
        ):
            response = self.client.get(path, **headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")
            self.assertEqual(
                response.json()["error"]["message"], "Invalid credentials or token"
            )

    def test_go_rate_limit_response_has_the_pinned_retry_header(self):
        from django.http import JsonResponse
        from django.test import RequestFactory

        from config.middleware import RequestSecurityMiddleware

        request = RequestFactory().post(
            "/api/v1/app/auth/login", data=b"{}", content_type="application/json"
        )
        response = RequestSecurityMiddleware(
            lambda _: JsonResponse(
                {"error": {"code": "RATE_LIMITED", "message": "Please try again later",
                           "request_id": "ignored", "details": {}}},
                status=429,
            )
        )(request)
        self.assertEqual(response["Retry-After"], "60")


if __name__ == "__main__":
    unittest.main()
