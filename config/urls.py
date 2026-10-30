import json

from django.db import connection, transaction
from django.http import HttpResponse, JsonResponse
from django.urls import path

from cow.api import api


def live(request):
    return JsonResponse({"data": {"status": "live"}})


def ready(request):
    try:
        # Statement timeout bounds a degraded but already connected PostgreSQL.
        # `postgres()` also has a bounded connect_timeout; neither failure leaks
        # connection details to the public readiness response.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '2000ms'")
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse(
            {
                "error": {
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": "Database unavailable",
                    "request_id": request.cow_request_id,
                }
            },
            status=503,
        )
    return JsonResponse({"data": {"status": "ready"}})


def version(request):
    return JsonResponse({"data": {"version": "admin-pricing-policy-1"}})


def openapi_yaml(request):
    import yaml

    from cow.openapi import add_diagnostics

    # Ninja returns an OpenAPISchema dict subclass that PyYAML's safe dumper
    # deliberately refuses. Round-tripping through JSON preserves OpenAPI data
    # while materializing only standard mapping/list/scalar values.
    schema = json.loads(json.dumps(add_diagnostics(api.get_openapi_schema())))
    return HttpResponse(
        yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), content_type="application/yaml"
    )


urlpatterns = [
    path("health/live", live),
    path("health/ready", ready),
    path("version", version),
    path("openapi.yaml", openapi_yaml),
    path("api/", api.urls),
]
