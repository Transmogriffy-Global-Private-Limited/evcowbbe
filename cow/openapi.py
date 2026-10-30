"""URLconf-owned operations that complement Ninja's generated API schema."""

from __future__ import annotations


def add_diagnostics(schema: dict) -> dict:
    """Add the three registered Django diagnostic operations to Ninja output."""
    paths = schema.setdefault("paths", {})
    paths.update(
        {
            "/health/live": {
                "get": {
                    "tags": ["Diagnostics"],
                    "operationId": "health_live",
                    "summary": "Liveness probe",
                    "security": [],
                    "responses": {"200": {"description": "Process is live"}},
                }
            },
            "/health/ready": {
                "get": {
                    "tags": ["Diagnostics"],
                    "operationId": "health_ready",
                    "summary": "Database readiness probe",
                    "security": [],
                    "responses": {
                        "200": {"description": "Database is ready"},
                        "503": {"description": "Database unavailable"},
                    },
                }
            },
            "/version": {
                "get": {
                    "tags": ["Diagnostics"],
                    "operationId": "version",
                    "summary": "Logical API version",
                    "security": [],
                    "responses": {"200": {"description": "Logical API version"}},
                }
            },
        }
    )
    return schema
