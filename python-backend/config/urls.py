import json
from django.http import HttpResponse, JsonResponse
from django.urls import path
from cow.api import api


def live(request):
    return JsonResponse({"data": {"status": "live"}})


def ready(request):
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": "Database unavailable", "request_id": request.cow_request_id}}, status=503)
    return JsonResponse({"data": {"status": "ready"}})


def version(request):
    return JsonResponse({"data": {"version": "cow-python-foundation-1", "hal_contract": "cow-hal.v1"}})


def openapi_yaml(request):
    import yaml
    schema = api.get_openapi_schema()
    return HttpResponse(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), content_type="application/yaml")

urlpatterns = [
    path("health/live", live), path("health/ready", ready), path("version", version),
    path("openapi.yaml", openapi_yaml), path("api/", api.urls),
]
