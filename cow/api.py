"""HTTP layer: stable FE envelopes, no implicit principal selection or ORM writes."""
from uuid import UUID
from typing import Literal
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.core.exceptions import ValidationError
from ninja import NinjaAPI, Router, Schema
from ninja.errors import ValidationError as NinjaValidationError, AuthenticationError
from ninja.security import HttpBearer
from pydantic import ConfigDict, Field
from cow.errors import DomainError, reject
from cow.models import (Account, Principal, AuthSession, CustomerVehicle, CustomerAddress,
                        ServiceJob, ServiceRequest, Quote, PricingPolicy, ServiceVehicle, Charger, HALCommand,
                        AdminRole, AdminOverride, AuditEvent)
from cow.services import auth, jobs, idempotency, rbac
from cow.hal.ingress import accept_signed_fact

class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Credentials(StrictSchema):
    email: str
    password: str

class TokenPayload(StrictSchema):
    token: str

class RefreshPayload(StrictSchema):
    refresh_token: str

class PasswordChange(StrictSchema):
    current_password: str
    new_password: str

class VehiclePayload(StrictSchema):
    make: str = Field(min_length=1,max_length=120)
    model: str = Field(min_length=1,max_length=120)
    registration_number: str = Field(default="",max_length=32)
    label: str = Field(default="",max_length=100)
    connector_types: list[str] = Field(default_factory=list,max_length=16)
    supports_ac: bool = True
    supports_dc: bool = False
    max_ac_power_w: int | None = Field(default=None,gt=0)
    max_dc_power_w: int | None = Field(default=None,gt=0)
    saved: bool = True

class AddressPayload(StrictSchema):
    address: str = Field(min_length=5,max_length=700)
    latitude: float = Field(ge=-90,le=90)
    longitude: float = Field(ge=-180,le=180)
    label: str = Field(default="",max_length=80)
    instructions: str = Field(default="",max_length=1000)
    saved: bool = True

class ServiceRequestPayload(StrictSchema):
    vehicle_id: UUID
    address_id: UUID
    requested_energy_wh: int = Field(gt=0,le=500000)

class HALCommandPayload(StrictSchema):
    charger_id: UUID
    connector_number: int = Field(gt=0)

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

api = NinjaAPI(title="TransEV Charger on Wheels CMS", version="0.1.0", description=(
    "Independent mobile charging business API. Go HAL boundary: cow-hal.v1. "
    "HAL acceptance is NOT physical start. Existing Go FE routes are NOT declared compatible until parity gate passes."),
    docs_url="/swagger/", openapi_url="/openapi.json")

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
    return api.create_response(request,{"error":{"code":"UNAUTHORIZED","message":"Authentication required",
        "request_id":request.cow_request_id,"details":{}}},status=401)

class PlaneBearer(HttpBearer):
    def __init__(self, plane):
        super().__init__()
        self.plane = plane
    def authenticate(self, request, token):
        return auth.authenticate(token,self.plane)

app = Router(auth=PlaneBearer("app"),tags=["Customer"])
driver = Router(auth=PlaneBearer("driver"),tags=["Driver"])
admin = Router(auth=PlaneBearer("admin"),tags=["Admin"])

# Route registration shares handlers, but the principal comes from verified plane bearer.
def mount_identity(router, plane):
    @router.post("/auth/login",auth=None,operation_id=f"{plane}_login")
    def login(request, payload:Credentials):
        return {"data":auth.login(plane,payload.email,payload.password)}

    @router.post("/auth/refresh",auth=None,operation_id=f"{plane}_refresh")
    def refresh(request,payload:RefreshPayload):
        response,error=auth.refresh(plane,payload.refresh_token)
        if error:
            reject(403 if error=="PRINCIPAL_PLANE_MISMATCH" else 401,error,"Refresh refused")
        return {"data":response}

    @router.get("/me",operation_id=f"{plane}_me")
    def me(request):
        principal = request.auth
        account=principal.account
        return {"data":{"account_id":str(account.id),"principal_id":str(principal.id),
            "plane":plane,"status":principal.status,"email":account.email,
            "email_verified":account.email_verified,"two_factor_enabled":account.two_factor_enabled}}

    @router.post("/auth/logout",operation_id=f"{plane}_logout")
    def logout(request):
        return {"data":auth.logout(request.headers["Authorization"].removeprefix("Bearer "))}

    @router.post("/auth/logout-all",operation_id=f"{plane}_logout_all")
    def logout_all(request):
        return {"data":auth.logout(request.headers["Authorization"].removeprefix("Bearer "),True)}

    @router.post("/auth/password/change",operation_id=f"{plane}_change_password")
    def change_password(request,payload:PasswordChange):
        return {"data":auth.change_password(request.auth,payload.current_password,payload.new_password)}

    if plane != "admin":
        @router.post("/auth/register",auth=None,response={202:dict},operation_id=f"{plane}_register")
        def register(request,payload:Credentials):
            return 202,{"data":auth.register(plane,payload.email,payload.password)}

for route,plane in [(app,"app"),(driver,"driver"),(admin,"admin")]:
    mount_identity(route,plane)

@api.post("/v1/auth/verify-email",auth=None,tags=["Auth"])
def verify_email(request,payload:TokenPayload):
    return {"data":auth.verify_email(payload.token)}


def vehicle_json(v):
    return {"id":str(v.id),"make":v.make,"model":v.model,"label":v.label,
      "registration_number":v.registration_number,"connector_types":v.connector_types,
      "supports_ac":v.supports_ac,"supports_dc":v.supports_dc,
      "max_ac_power_w":v.max_ac_power_w,"max_dc_power_w":v.max_dc_power_w,
      "saved":v.saved,"active":v.active,"version":v.version}

@app.get("/vehicles")
def list_vehicles(request):
    return {"data":[vehicle_json(v) for v in CustomerVehicle.objects.filter(owner=request.auth,active=True).order_by("created_at","id")[:100]]}

@app.post("/vehicles",response={201:dict})
def create_vehicle(request,payload:VehiclePayload):
    p=payload.model_dump()
    if not p["supports_ac"] and not p["supports_dc"]:
        reject(422,"VEHICLE_INVALID","At least one charging mode is required")
    with transaction.atomic():
        v=CustomerVehicle.objects.create(owner=request.auth,**p)
        AuditEvent.objects.create(actor=request.auth,target_type="CustomerVehicle",target_id=v.id,action="CREATE")
    return 201,{"data":vehicle_json(v)}


def address_json(a):
    return {"id":str(a.id),"address":a.address,"latitude":str(a.latitude),"longitude":str(a.longitude),
            "label":a.label,"instructions":a.instructions,"saved":a.saved,"active":a.active,"version":a.version}

@app.get("/addresses")
def list_addresses(request):
    return {"data":[address_json(a) for a in CustomerAddress.objects.filter(owner=request.auth,active=True).order_by("created_at","id")[:100]]}

@app.post("/addresses",response={201:dict})
def create_address(request,payload:AddressPayload):
    from decimal import Decimal
    p=payload.model_dump()
    # Convert validated JSON numeric to exact decimal before durable persistence.
    p["latitude"] = Decimal(str(p["latitude"]))
    p["longitude"] = Decimal(str(p["longitude"]))
    with transaction.atomic():
        a=CustomerAddress.objects.create(owner=request.auth,**p)
        AuditEvent.objects.create(actor=request.auth,target_type="CustomerAddress",target_id=a.id,action="CREATE")
    return 201,{"data":address_json(a)}

@app.post("/service-requests",response={201:dict})
def request_service(request,payload:ServiceRequestPayload):
    p=payload.model_dump(mode="json")
    def operation():
        r=jobs.create_request(request.auth,payload.vehicle_id,payload.address_id,payload.requested_energy_wh)
        return 201,{"data":{"id":str(r.id),"state":r.state,"vehicle_snapshot":r.vehicle_snapshot,"address_snapshot":r.address_snapshot}}
    return idempotency.execute(request.auth,"service-request",request.headers.get("Idempotency-Key"),p,operation)

@app.post("/service-requests/{request_id}/quote",response={201:dict})
def quote_request(request,request_id:UUID):
    def op():
        q=jobs.create_quote(request.auth,request_id)
        return 201,{"data":{"id":str(q.id),"request_id":str(q.request_id),"total_paise":q.total_paise,"currency":q.currency,"pricing_snapshot":q.pricing_snapshot,"expires_at":q.expires_at.isoformat()}}
    return idempotency.execute(request.auth,f"quote:{request_id}",request.headers.get("Idempotency-Key"),{"request_id":str(request_id)},op)

@app.post("/quotes/{quote_id}/accept",response={201:dict})
def accept_quote(request,quote_id:UUID):
    def op():
        job=jobs.accept_quote(request.auth,quote_id)
        return 201,{"data":{"id":str(job.id),"state":job.state,"version":job.version}}
    return idempotency.execute(request.auth,f"accept:{quote_id}",request.headers.get("Idempotency-Key"),{"quote_id":str(quote_id)},op)

@app.get("/jobs/{job_id}")
def get_job(request,job_id:UUID):
    job=ServiceJob.objects.select_related("request","accepted_quote").filter(id=job_id,request__customer=request.auth).first()
    if not job:reject(404,"NOT_FOUND","Job not found")
    return {"data":{"id":str(job.id),"state":job.state,"version":job.version,
           "vehicle_snapshot":job.request.vehicle_snapshot,"address_snapshot":job.request.address_snapshot,
           "quoted_total_paise":job.accepted_quote.total_paise}}

@admin.get("/rbac/me")
def my_permissions(request):
    roles=set(AdminRole.objects.filter(principal=request.auth).values_list("role",flat=True))
    overrides=dict(AdminOverride.objects.filter(principal=request.auth).values_list("capability","decision"))
    return {"data":{"roles":sorted(roles),"effective_capabilities":sorted(c for c in rbac.CAPABILITIES if rbac.permitted(roles,overrides,c))}}

@admin.get("/rbac/catalog")
def catalog(request):
    rbac.require(request.auth,"system.settings.read")
    return {"data":{"capabilities":list(rbac.CAPABILITIES),"roles":{k:sorted(v) for k,v in rbac.ROLE_DEFAULTS.items()}}}

@admin.post("/rbac/accounts/{account_id}/roles",response={201:dict})
def assign_role(request,account_id:UUID,payload:RolePayload):
    rbac.require(request.auth,"system.settings.manage")
    if payload.role not in rbac.ROLE_DEFAULTS:reject(400,"ROLE_UNKNOWN","Invalid role")
    with transaction.atomic():
        p=Principal.objects.filter(account_id=account_id,plane="admin").first()
        if not p:reject(404,"NOT_FOUND","Admin account not found")
        role,created=AdminRole.objects.get_or_create(principal=p,role=payload.role)
        AuditEvent.objects.create(actor=request.auth,target_type="AdminRole",target_id=role.id,
          action="ASSIGN_ROLE",details={"role":payload.role,"reason":payload.reason})
    return 201,{"data":{"role":payload.role,"created":created}}

@admin.put("/rbac/accounts/{account_id}/overrides/{capability}")
def set_override(request,account_id:UUID,capability:str,payload:OverridePayload):
    rbac.require(request.auth,"system.settings.manage")
    if capability not in rbac.CAPABILITIES:reject(400,"CAPABILITY_UNKNOWN","Invalid capability")
    with transaction.atomic():
        p=Principal.objects.filter(account_id=account_id,plane="admin").first()
        if not p:reject(404,"NOT_FOUND","Admin account not found")
        obj,_=AdminOverride.objects.update_or_create(principal=p,capability=capability,defaults={"decision":payload.decision})
        AuditEvent.objects.create(actor=request.auth,target_type="AdminOverride",target_id=obj.id,
             action="SET_OVERRIDE",details={"capability":capability,"decision":payload.decision,"reason":payload.reason})
    return {"data":{"capability":capability,"decision":payload.decision}}

@admin.get("/vehicles")
def admin_vehicles(request):
    rbac.require(request.auth,"fleet.read")
    return {"data":[{"id":str(v.id),"asset_tag":v.asset_tag,"make":v.make,"model":v.model,"active":v.active,"version":v.version} for v in ServiceVehicle.objects.order_by("asset_tag")[:100]]}

@admin.post("/vehicles",response={201:dict})
def admin_create_vehicle(request,payload:FleetVehiclePayload):
    rbac.require(request.auth,"fleet.manage")
    with transaction.atomic():
        v=ServiceVehicle.objects.create(**payload.model_dump())
        AuditEvent.objects.create(actor=request.auth,target_type="ServiceVehicle",target_id=v.id,action="CREATE")
    return 201,{"data":{"id":str(v.id),"asset_tag":v.asset_tag,"version":v.version}}

@admin.get("/chargers")
def admin_chargers(request):
    rbac.require(request.auth,"fleet.read")
    return {"data":[{"id":str(v.id),"asset_tag":v.asset_tag,"hal_identity":v.hal_identity,"active":v.active} for v in Charger.objects.order_by("asset_tag")[:100]]}

@admin.get("/pricing/policies")
def pricing_policies(request):
    rbac.require(request.auth,"pricing.read")
    return {"data":[{"id":str(p.id),"name":p.name,"revision":p.revision,"status":p.status,"base_service_paise":p.base_service_paise,"per_kwh_paise":p.per_kwh_paise} for p in PricingPolicy.objects.order_by("name","-revision")[:100]]}

@admin.post("/pricing/policies",response={201:dict})
def pricing_create(request,payload:PricingPayload):
    rbac.require(request.auth,"pricing.manage")
    with transaction.atomic():
        latest=PricingPolicy.objects.filter(name=payload.name).order_by("-revision").first()
        p=PricingPolicy.objects.create(**payload.model_dump(),revision=(latest.revision+1 if latest else 1))
        AuditEvent.objects.create(actor=request.auth,target_type="PricingPolicy",target_id=p.id,action="CREATE")
    return 201,{"data":{"id":str(p.id),"revision":p.revision,"status":p.status}}

@admin.post("/pricing/policies/{policy_id}/publish")
def pricing_publish(request,policy_id:UUID):
    rbac.require(request.auth,"pricing.manage")
    with transaction.atomic():
        p=PricingPolicy.objects.select_for_update().filter(id=policy_id).first()
        if not p:reject(404,"NOT_FOUND","Policy not found")
        if p.status!="DRAFT":reject(409,"POLICY_STATE_CONFLICT","Policy cannot publish")
        # Freeze active policy, prevent post-publication edit through API.
        previous=PricingPolicy.objects.select_for_update().filter(status="ACTIVE")
        now=timezone.now()
        previous.update(status="RETIRED",effective_to=now)
        p.status="ACTIVE";p.effective_from=now
        p.save(update_fields=["status","effective_from","updated_at"])
        AuditEvent.objects.create(actor=request.auth,target_type="PricingPolicy",target_id=p.id,action="PUBLISH")
    return {"data":{"id":str(p.id),"status":p.status}}

@admin.get("/jobs")
def admin_jobs(request):
    rbac.require(request.auth,"dispatch.read")
    return {"data":[{"id":str(j.id),"state":j.state,"version":j.version} for j in ServiceJob.objects.order_by("-created_at","-id")[:100]]}

@admin.get("/jobs/{job_id}")
def admin_get_job(request,job_id:UUID):
    rbac.require(request.auth,"dispatch.read")
    job=ServiceJob.objects.filter(id=job_id).first()
    if not job:reject(404,"NOT_FOUND","Job not found")
    return {"data":{"id":str(job.id),"state":job.state,"version":job.version,"request_id":str(job.request_id)}}

@admin.post("/jobs/{job_id}/charging/start",response={202:dict})
def start_charging(request,job_id:UUID,payload:HALCommandPayload):
    rbac.require(request.auth,"charging.manage")
    def op():
        with transaction.atomic():
            job=ServiceJob.objects.select_for_update().filter(id=job_id).first()
            charger=Charger.objects.filter(id=payload.charger_id,active=True).first()
            if not job or not charger:reject(404,"NOT_FOUND","Job or charger not found")
            if job.state!="SERVICE_PREPARING":reject(409,"JOB_STATE_CONFLICT","Job not physically ready")
            # Must reference assigned and verified duty/charger, not an arbitrary Admin selection.
            if not job.driver_duty_id or job.driver_duty.charger_id!=charger.id:
                reject(422,"DUTY_CHARGER_MISMATCH","Driver duty does not own charger")
            c=HALCommand.objects.create(job=job,charger=charger,action="StartCharging",
                           params={"connector_number":payload.connector_number})
            return 202,{"data":{"operation_id":str(c.id),"state":c.state}}
    return idempotency.execute(request.auth,f"charge-start:{job_id}",request.headers.get("Idempotency-Key"),payload.model_dump(mode="json"),op,retention_hours=168)

@api.post("/v1/internal/hal/facts",auth=None,tags=["HAL"],response={202:dict})
def hal_fact(request):
    return 202,{"data":accept_signed_fact(request.body,request.headers.get("X-HAL-Timestamp",""),
                                           request.headers.get("X-HAL-Signature",""))}

api.add_router("/v1/app",app)
api.add_router("/v1/driver",driver)
api.add_router("/v1/admin",admin)

# Second-stage auth contract: these operations intentionally require real mail transport.
class EmailPayload(StrictSchema):
    email: str

class ResetPayload(StrictSchema):
    token: str
    new_password: str

class OTPPayload(StrictSchema):
    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")

class ChallengePayload(StrictSchema):
    challenge_id: UUID

class PasswordConfirm(StrictSchema):
    password: str

@api.post("/v1/auth/resend-verification",auth=None,tags=["Auth"])
def resend_verification(request,payload:EmailPayload):
    return {"data":auth.resend_verification(payload.email)}

@api.post("/v1/auth/forgot-password",auth=None,tags=["Auth"])
def forgot_password(request,payload:EmailPayload):
    return {"data":auth.forgot_password(payload.email)}

@api.post("/v1/auth/reset-password",auth=None,tags=["Auth"])
def reset_password(request,payload:ResetPayload):
    return {"data":auth.reset_password(payload.token,payload.new_password)}

# register extra handlers on each plane Router before URL resolution (router mutation
# after add_router is supported by Ninja's lazy urlpatterns, but use explicit source gate).
def mount_otp(route,plane):
    @route.post("/auth/verify-otp",auth=None,operation_id=f"{plane}_verify_otp")
    def verify_otp(request,payload:OTPPayload):
        return {"data":auth.verify_login_otp(str(payload.challenge_id),payload.code,plane)}

    @route.post("/auth/2fa/resend",auth=None,operation_id=f"{plane}_resend_otp")
    def resend_otp(request,payload:ChallengePayload):
        return {"data":auth.resend_otp(str(payload.challenge_id),plane)}

    @route.post("/auth/2fa/enable/begin",operation_id=f"{plane}_enable_begin")
    def enable_begin(request,payload:PasswordConfirm):
        return {"data":auth.begin_two_factor(request.auth,payload.password,True)}

    @route.post("/auth/2fa/disable/begin",operation_id=f"{plane}_disable_begin")
    def disable_begin(request,payload:PasswordConfirm):
        return {"data":auth.begin_two_factor(request.auth,payload.password,False)}

    @route.post("/auth/2fa/enable/confirm",operation_id=f"{plane}_enable_confirm")
    def enable_confirm(request,payload:OTPPayload):
        return {"data":auth.confirm_two_factor(request.auth,str(payload.challenge_id),payload.code,True)}

    @route.post("/auth/2fa/disable/confirm",operation_id=f"{plane}_disable_confirm")
    def disable_confirm(request,payload:OTPPayload):
        return {"data":auth.confirm_two_factor(request.auth,str(payload.challenge_id),payload.code,False)}

for route,plane in [(app,"app"),(driver,"driver"),(admin,"admin")]:
    mount_otp(route,plane)
