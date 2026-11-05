"""HTTP layer: stable FE envelopes, no implicit principal selection or ORM writes."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from django.db import transaction
from ninja import NinjaAPI, Router, Schema
from ninja.errors import AuthenticationError
from ninja.errors import ValidationError as NinjaValidationError
from ninja.openapi.docs import Swagger
from ninja.security import HttpBearer
from pydantic import ConfigDict, Field

from cow.common.contracts import AckEnvelope
from cow.common.errors import DomainError, reject
from cow.admin.fleet import fleet_router
from cow.hal.ingress import accept_signed_fact
from cow.models import (
    Charger,
    HALCommand,
    ServiceJob,
)
from cow.services import auth, idempotency
from cow.admin.jobs import HALCommandPayload as HALCommandPayload
from cow.users.charging import ServiceRequestPayload as ServiceRequestPayload  # compatibility export


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Credentials(StrictSchema):
    email: str = Field(max_length=254, json_schema_extra={"format": "email"})
    # Go validates password policy in the authentication service, after route
    # decoding.  Login deliberately accepts any JSON string so a bad password
    # follows the same invalid-credential path rather than a schema shortcut.
    password: str = Field(json_schema_extra={"format": "password"})

class TokenPayload(StrictSchema):
    # encoding/json leaves a missing struct field as its zero value.  The Go
    # service then returns its normal invalid-token error, rather than a
    # decoder/schema error.  Keep that observable ordering.
    token: str = Field(default="", min_length=43, max_length=43)

class RefreshPayload(StrictSchema):
    refresh_token: str = Field(default="", min_length=43, max_length=43)

class PasswordChange(StrictSchema):
    current_password: str = Field(json_schema_extra={"format": "password"})
    new_password: str = Field(json_schema_extra={"format": "password"})


class AcceptedData(StrictSchema):
    accepted: Literal[True]


class AcceptedEnvelope(StrictSchema):
    data: AcceptedData


class TokenPair(StrictSchema):
    access_token: str = Field(min_length=43, max_length=43)
    refresh_token: str = Field(min_length=43, max_length=43)
    token_type: Literal["Bearer"]
    access_expires_at: datetime
    refresh_expires_at: datetime
    session_id: UUID
    plane: Literal["app", "driver", "admin"]


class TokenEnvelope(StrictSchema):
    data: TokenPair


class LoginWithTokens(StrictSchema):
    two_factor_required: Literal[False]
    tokens: TokenPair


class LoginWithChallenge(StrictSchema):
    two_factor_required: Literal[True]
    challenge_id: UUID


class LoginEnvelope(StrictSchema):
    data: Annotated[LoginWithTokens | LoginWithChallenge, Field(discriminator="two_factor_required")]


class RegistrationData(StrictSchema):
    message: Literal["If eligible, a verification email has been queued"]
    plane: Literal["app", "driver"]


class RegistrationEnvelope(StrictSchema):
    data: RegistrationData


class ProfileData(StrictSchema):
    id: UUID
    email: str = Field(max_length=254, json_schema_extra={"format": "email"})
    plane: Literal["app", "driver", "admin"]
    principal_status: Literal["ACTIVE", "PENDING", "SUSPENDED"]
    email_verified: bool
    two_factor_enabled: bool


class ProfileEnvelope(StrictSchema):
    data: ProfileData


class BeginTwoFactorData(StrictSchema):
    challenge_id: UUID
    message: Literal["Check email for one-time code"]


class BeginTwoFactorEnvelope(StrictSchema):
    data: BeginTwoFactorData

class FleetVehiclePayload(StrictSchema):
    asset_tag: str = Field(min_length=2,max_length=64)
    make: str = Field(min_length=1,max_length=120)
    model: str = Field(min_length=1,max_length=120)
    registration_number: str = ""

class PricingPayload(StrictSchema):
    name: str = Field(min_length=3,max_length=120)
    base_service_paise: int = Field(ge=0)
    per_kwh_paise: int = Field(ge=0)

class RolePayload(StrictSchema):
    role: str
    reason: str = Field(min_length=8,max_length=500)

class OverridePayload(StrictSchema):
    decision: Literal["ALLOW","DENY"]
    reason: str = Field(min_length=8,max_length=500)

class FrontendDocumentedAPI(NinjaAPI):
    """One source for UI, JSON and YAML with unchanged business handlers."""

    def get_openapi_schema(self, *, path_prefix=None, path_params=None):
        from cow.fe_openapi import decorate_fe_schema
        from cow.openapi import add_diagnostics

        native = super().get_openapi_schema(
            path_prefix=path_prefix, path_params=path_params
        )
        return decorate_fe_schema(add_diagnostics(native))


api = FrontendDocumentedAPI(title="TransEV Charger on Wheels CMS", version="0.1.0", description=(
    "Independent mobile charging business API. Go HAL boundary: cow-hal.v1. "
    "HAL acceptance is NOT physical start. Existing Go FE routes are NOT declared compatible until parity gate passes."),
    docs_url="/swagger/", openapi_url="/openapi.json",
    docs=Swagger(settings={
        "docExpansion": "none", "filter": True, "deepLinking": True,
        "displayRequestDuration": True, "displayOperationId": True,
        "persistAuthorization": False,
    }),
)

@api.exception_handler(DomainError)
def domain_error(request, exc):
    return api.create_response(request,{"error": {"code":exc.code,"message":str(exc.message),
        "request_id":request.cow_request_id,"details":{}}},status=exc.status_code)

@api.exception_handler(NinjaValidationError)
def validation_error(request, exc):
    return api.create_response(request,{"error": {"code":"VALIDATION_FAILED", "message":"Invalid request fields",
        "request_id":request.cow_request_id,"details":{}}},status=400)

@api.exception_handler(AuthenticationError)
def authentication_error(request, exc):
    # The frozen Go handler maps a missing bearer through auth.ErrInvalid,
    # rather than emitting framework-specific authentication wording.
    return api.create_response(request,{"error":{"code":"INVALID_CREDENTIALS","message":"Invalid credentials or token",
        "request_id":request.cow_request_id,"details":{}}},status=401)

class PlaneBearer(HttpBearer):
    # These are opaque, fixed-width credentials, as in the frozen Go wire
    # contract.  The plane is verified by authenticate(), not inferred from a
    # documentation-only security scheme.
    openapi_bearerFormat = "opaque"

    def __init__(self, plane):
        super().__init__()
        self.plane = plane
    def authenticate(self, request, token):
        return auth.authenticate(token,self.plane)


def _go_authenticated_json(request, *, decoded: bool = True):
    # Do not parse legacy protected JSON before its bearer authentication.
    # Logout is the exception: Go's post() checks only Content-Type and
    # deliberately ignores the body.  Routes with a decoded request model use
    # the same 32 KiB limit as Server.decode().
    if request.content_type.split(";", 1)[0].lower() != "application/json":
        reject(415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")
    if decoded and (int(request.META.get("CONTENT_LENGTH") or 0) > 32768 or len(request.body) > 32768):
        reject(400, "VALIDATION_FAILED", "Invalid JSON request")


# Route registration shares handlers, but the principal comes from verified plane bearer.
def mount_identity(router, plane):
    @router.post("/auth/login", auth=None, response=LoginEnvelope, operation_id=f"{plane}_login")
    def login(request, payload:Credentials):
        return {"data":auth.login(plane,payload.email,payload.password)}

    @router.post("/auth/refresh", auth=None, response=TokenEnvelope, operation_id=f"{plane}_refresh")
    def refresh(request,payload:RefreshPayload):
        response,error=auth.refresh(plane,payload.refresh_token)
        if error:
            status = 403 if error == "PRINCIPAL_PLANE_MISMATCH" else 401
            message = (
                "Access not permitted for this principal"
                if error == "PRINCIPAL_PLANE_MISMATCH"
                else "Session revoked; sign in again"
                if error == "REFRESH_TOKEN_REUSED"
                else "Invalid credentials or token"
            )
            reject(status, error, message)
        return {"data":response["tokens"]}

    @router.get("/me", response=ProfileEnvelope, operation_id=f"{plane}_me")
    def me(request):
        principal = request.auth
        account = principal.account
        return {"data": {
            "id": str(account.id),
            "email": account.email,
            "plane": plane,
            "principal_status": principal.status,
            "email_verified": account.email_verified,
            "two_factor_enabled": account.two_factor_enabled,
        }}

    @router.post("/auth/logout", response=AckEnvelope, operation_id=f"{plane}_logout")
    def logout(request):
        _go_authenticated_json(request, decoded=False)
        auth.logout(request.headers["Authorization"].removeprefix("Bearer "))
        return {"data":{"ok":True}}

    @router.post("/auth/logout-all", response=AckEnvelope, operation_id=f"{plane}_logout_all")
    def logout_all(request):
        _go_authenticated_json(request, decoded=False)
        auth.logout(request.headers["Authorization"].removeprefix("Bearer "),True)
        return {"data":{"ok":True}}

    @router.post("/auth/password/change", response=AckEnvelope, operation_id=f"{plane}_change_password")
    def change_password(request,payload:PasswordChange):
        _go_authenticated_json(request)
        auth.change_password(request.auth,payload.current_password,payload.new_password)
        return {"data":{"ok":True}}

    if plane != "admin":
        @router.post(
            "/auth/register",
            auth=None,
            response={202: RegistrationEnvelope},
            operation_id=f"{plane}_register",
        )
        def register(request,payload:Credentials):
            return 202,{"data":auth.register(plane,payload.email,payload.password)}

# Admin identity is mounted inside cow.admin.router

@api.post("/v1/auth/verify-email", auth=None, tags=["Auth"], response=AckEnvelope)
def verify_email(request,payload:TokenPayload):
    auth.verify_email(payload.token)
    return {"data":{"ok":True}}


# Annex 44 RBAC routes registered below.

# Versioned tariff routes registered below.

@api.post("/v1/internal/hal/facts",auth=None,tags=["HAL"],response={202:dict})
def hal_fact(request):
    return 202,{"data":accept_signed_fact(request.body,request.headers.get("X-HAL-Timestamp",""),
                                           request.headers.get("X-HAL-Signature",""))}


# Second-stage auth contract: these operations intentionally require real mail transport.
class EmailPayload(StrictSchema):
    email: str = Field(max_length=254, json_schema_extra={"format": "email"})

class ResetPayload(StrictSchema):
    token: str = Field(default="", min_length=43, max_length=43)
    new_password: str = Field(default="", json_schema_extra={"format": "password"})

class OTPPayload(StrictSchema):
    challenge_id: UUID
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")

class ChallengePayload(StrictSchema):
    challenge_id: UUID

class PasswordConfirm(StrictSchema):
    password: str = Field(json_schema_extra={"format": "password"})

@api.post("/v1/auth/resend-verification", auth=None, tags=["Auth"], response=AcceptedEnvelope)
def resend_verification(request,payload:EmailPayload):
    auth.resend_verification(payload.email)
    return {"data":{"accepted":True}}

@api.post("/v1/auth/forgot-password", auth=None, tags=["Auth"], response=AcceptedEnvelope)
def forgot_password(request,payload:EmailPayload):
    auth.forgot_password(payload.email)
    return {"data":{"accepted":True}}

@api.post("/v1/auth/reset-password", auth=None, tags=["Auth"], response=AckEnvelope)
def reset_password(request,payload:ResetPayload):
    auth.reset_password(payload.token,payload.new_password)
    return {"data":{"ok":True}}

# register extra handlers on each plane Router before URL resolution (router mutation
# after add_router is supported by Ninja's lazy urlpatterns, but use explicit source gate).
def mount_otp(route,plane):
    @route.post("/auth/verify-otp", auth=None, response=TokenEnvelope, operation_id=f"{plane}_verify_otp")
    def verify_otp(request,payload:OTPPayload):
        result = auth.verify_login_otp(str(payload.challenge_id),payload.code,plane)
        return {"data":result["tokens"]}

    @route.post("/auth/2fa/resend", auth=None, response=AckEnvelope, operation_id=f"{plane}_resend_otp")
    def resend_otp(request,payload:ChallengePayload):
        auth.resend_otp(str(payload.challenge_id),plane)
        return {"data":{"ok":True}}

    @route.post("/auth/2fa/enable/begin", response=BeginTwoFactorEnvelope, operation_id=f"{plane}_enable_begin")
    def enable_begin(request,payload:PasswordConfirm):
        _go_authenticated_json(request)
        return {"data":auth.begin_two_factor(request.auth,payload.password,True)}

    @route.post("/auth/2fa/disable/begin", response=BeginTwoFactorEnvelope, operation_id=f"{plane}_disable_begin")
    def disable_begin(request,payload:PasswordConfirm):
        _go_authenticated_json(request)
        return {"data":auth.begin_two_factor(request.auth,payload.password,False)}

    @route.post("/auth/2fa/enable/confirm", response=AckEnvelope, operation_id=f"{plane}_enable_confirm")
    def enable_confirm(request,payload:OTPPayload):
        _go_authenticated_json(request)
        auth.confirm_two_factor(request.auth,str(payload.challenge_id),payload.code,True)
        return {"data":{"ok":True}}

    @route.post("/auth/2fa/disable/confirm", response=AckEnvelope, operation_id=f"{plane}_disable_confirm")
    def disable_confirm(request,payload:OTPPayload):
        _go_authenticated_json(request)
        auth.confirm_two_factor(request.auth,str(payload.challenge_id),payload.code,False)
        return {"data":{"ok":True}}

# Admin OTP is mounted inside cow.admin.router

# Per-role routers are assembled once, with central auth and single schema.
from cow.admin.router import build_router as build_admin_router  # noqa: E402
from cow.users.router import build_router as build_users_router  # noqa: E402
from cow.drivers.router import build_router as build_drivers_router  # noqa: E402

app = build_users_router(PlaneBearer("app"), mount_identity, mount_otp)
driver = build_drivers_router(PlaneBearer("driver"), mount_identity, mount_otp)
admin = build_admin_router(PlaneBearer("admin"), mount_identity, mount_otp)

api.add_router("/v1/app", app)
api.add_router("/v1/driver", driver)
api.add_router("/v1/admin", admin)
api.add_router("/v1/admin", fleet_router)
