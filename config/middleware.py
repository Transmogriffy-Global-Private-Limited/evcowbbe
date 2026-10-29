from uuid import UUID, uuid4
from django.conf import settings
from django.http import JsonResponse

class RequestSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        proposed = request.headers.get("X-Request-ID", "")
        try:
            request.cow_request_id = str(UUID(proposed)) if proposed else str(uuid4())
        except ValueError:
            request.cow_request_id = str(uuid4())
        origin = request.headers.get("Origin", "")
        if request.method == "OPTIONS" and request.path.startswith("/api/v1/"):
            if origin not in settings.COW_CORS_ORIGINS:
                result = JsonResponse({"error": {"code": "ORIGIN_FORBIDDEN", "message": "Origin not allowed", "request_id": request.cow_request_id}}, status=403)
            else:
                result = JsonResponse({}, status=204)
        else:
            result = self.get_response(request)
        result["X-Request-ID"] = request.cow_request_id
        result["Cache-Control"] = "no-store"
        result["X-Content-Type-Options"] = "nosniff"
        result["Referrer-Policy"] = "no-referrer"
        result["X-Frame-Options"] = "DENY"
        if settings.ENV != "development":
            result["Strict-Transport-Security"] = "max-age=31536000"
        if origin in settings.COW_CORS_ORIGINS:
            result["Access-Control-Allow-Origin"] = origin
            result["Vary"] = "Origin"
            result["Access-Control-Allow-Methods"] = "GET,POST,PATCH,PUT,DELETE,OPTIONS"
            result["Access-Control-Allow-Headers"] = "Content-Type,Authorization,Idempotency-Key,If-Match,X-Request-ID"
            result["Access-Control-Max-Age"] = "300"
        return result
