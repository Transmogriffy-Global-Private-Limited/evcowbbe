"""Swagger HTML and the actual local static dependencies are one API contract."""
from __future__ import annotations

import os
import re
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


class SwaggerUIRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import django
        django.setup()
        from django.test import Client
        cls.client = Client()

    def test_docs_html_openapi_and_local_assets(self):
        from django.conf import settings

        self.assertFalse(settings.DEBUG)
        self.assertEqual(settings.STATIC_URL, "/static/")
        self.assertTrue(settings.TEMPLATES)

        page = self.client.get("/api/swagger/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page["Content-Type"])
        html = page.content.decode("utf-8")
        self.assertIn("swagger", html.lower())

        assets = set(re.findall(
            r'''(?:href|src)\s*=\s*["'](/static/ninja/[^"']+)["']''',
            html,
            flags=re.IGNORECASE,
        ))
        self.assertTrue(assets, "Swagger must reference bundled local CSS/JS assets")
        self.assertTrue(any(url.endswith(".css") for url in assets))
        self.assertTrue(any(url.endswith(".js") for url in assets))
        for url in sorted(assets):
            with self.subTest(asset=url):
                response = self.client.get(url)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(response["Content-Type"].split(";")[0],
                                  {"text/css", "text/javascript", "application/javascript", "image/png", "image/svg+xml", "image/x-icon", "application/octet-stream"})
                finally:
                    response.close()

        schema = self.client.get("/api/openapi.json")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("paths", schema.json())

    def test_static_scope_is_not_broadened(self):
        self.assertEqual(self.client.get("/static/ninja/not-a-real-asset.js").status_code, 404)
        self.assertEqual(self.client.post("/static/ninja/swagger-ui.css").status_code, 405)
        self.assertEqual(self.client.get("/static/other-project/file.css").status_code, 404)
