"""Admin job operations. Must preserve central RBAC and HAL admission."""
from uuid import UUID
from typing import Literal

from django.db import transaction
from ninja import Schema
from pydantic import ConfigDict, Field

from cow.common.errors import reject
from cow.models import Charger, HALCommand, ServiceJob
from cow.services import idempotency, rbac
from cow.services import operational_eligibility as admission

class HALCommandPayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    charger_id: UUID
    connector_number: int = Field(gt=0)


class ChargingCommandData(Schema):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    state: Literal["PENDING"]

class ChargingCommandEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: ChargingCommandData

def register_job_routes(router):
    @router.get("/jobs", operation_id="cow_api_admin_jobs")
    def admin_jobs(request):
        rbac.require(request.auth,"dispatch.read")
        return {"data":[{"id":str(j.id),"state":j.state,"version":j.version} for j in ServiceJob.objects.order_by("-created_at","-id")[:100]]}

    @router.get("/jobs/{job_id}", operation_id="cow_api_admin_get_job")
    def admin_get_job(request,job_id:UUID):
        rbac.require(request.auth,"dispatch.read")
        job=ServiceJob.objects.filter(id=job_id).first()
        if not job:
            reject(404, "NOT_FOUND", "Job not found")
        return {"data":{"id":str(job.id),"state":job.state,"version":job.version,"request_id":str(job.request_id)}}

    @router.post("/jobs/{job_id}/charging/start",response={202:ChargingCommandEnvelope},operation_id="cow_api_start_charging")
    def start_charging(request,job_id:UUID,payload:HALCommandPayload):
        rbac.require(request.auth,"charging.command")
        def op():
            with transaction.atomic():
                admission.fresh_admin(request.auth, "charging.command")
                admission.lock_gate()
                job = admission.assert_start_admissible(
                    job_id, payload.charger_id, payload.connector_number)
                c = HALCommand.objects.create(
                    job=job, charger_id=payload.charger_id, action="StartCharging",
                    params={"connector_number": payload.connector_number})
                return 202, {"data": {"operation_id": str(c.id), "state": c.state}}
        return idempotency.execute(request.auth,f"charge-start:{job_id}",request.headers.get("Idempotency-Key"),payload.model_dump(mode="json"),op,retention_hours=168)
