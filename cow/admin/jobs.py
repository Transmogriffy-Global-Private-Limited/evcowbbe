"""Admin job operations. Must preserve central RBAC and HAL admission."""
from uuid import UUID

from django.db import transaction
from ninja import Schema
from pydantic import ConfigDict, Field

from cow.common.errors import reject
from cow.models import Charger, HALCommand, ServiceJob
from cow.services import idempotency, rbac

class HALCommandPayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    charger_id: UUID
    connector_number: int = Field(gt=0)


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

    @router.post("/jobs/{job_id}/charging/start",response={202:dict},operation_id="cow_api_start_charging")
    def start_charging(request,job_id:UUID,payload:HALCommandPayload):
        rbac.require(request.auth,"charging.command")
        def op():
            with transaction.atomic():
                job=ServiceJob.objects.select_for_update().filter(id=job_id).first()
                charger=Charger.objects.filter(id=payload.charger_id,active=True).first()
                if not job or not charger:
                    reject(404, "NOT_FOUND", "Job or charger not found")
                if job.state != "SERVICE_PREPARING":
                    reject(409, "JOB_STATE_CONFLICT", "Job not physically ready")
                # Must reference assigned and verified duty/charger, not an arbitrary Admin selection.
                if not job.driver_duty_id or job.driver_duty.charger_id!=charger.id:
                    reject(422,"DUTY_CHARGER_MISMATCH","Driver duty does not own charger")
                c=HALCommand.objects.create(job=job,charger=charger,action="StartCharging",
                               params={"connector_number":payload.connector_number})
                return 202,{"data":{"operation_id":str(c.id),"state":c.state}}
        return idempotency.execute(request.auth,f"charge-start:{job_id}",request.headers.get("Idempotency-Key"),payload.model_dump(mode="json"),op,retention_hours=168)
