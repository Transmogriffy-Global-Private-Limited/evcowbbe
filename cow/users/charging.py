"""Customer service request and quote route declarations.
Service request intake and job reads remain active. Legacy non-Zone quote
creation and acceptance are retired until Zone commitment is available.
"""
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from cow.common.errors import reject
from cow.models import ServiceJob
from cow.services import idempotency, jobs


class ServiceRequestPayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    vehicle_id: UUID
    address_id: UUID
    requested_energy_wh: int = Field(gt=0,le=500000)


def register_charging_routes(router):
    @router.post("/service-requests",response={201:dict},operation_id="cow_api_request_service")
    def request_service(request,payload:ServiceRequestPayload):
        p=payload.model_dump(mode="json")
        def operation():
            r=jobs.create_request(request.auth,payload.vehicle_id,payload.address_id,payload.requested_energy_wh)
            return 201,{"data":{"id":str(r.id),"state":r.state,"vehicle_snapshot":r.vehicle_snapshot,"address_snapshot":r.address_snapshot}}
        return idempotency.execute(request.auth,"service-request",request.headers.get("Idempotency-Key"),p,operation)

    @router.post("/service-requests/{request_id}/quote",include_in_schema=False,response={201:dict},operation_id="cow_api_quote_request")
    def quote_request(request,request_id:UUID):
        reject(410, "LEGACY_QUOTE_RETIRED",
               "Only Zone composite tariffs are supported; customer commitment is pending")

    @router.post("/quotes/{quote_id}/accept",include_in_schema=False,response={201:dict},operation_id="cow_api_accept_quote")
    def accept_quote(request,quote_id:UUID):
        reject(410, "LEGACY_QUOTE_RETIRED",
               "Legacy quotes cannot be accepted; Zone commitment is pending")

    @router.get("/jobs/{job_id}",operation_id="cow_api_get_job")
    def get_job(request,job_id:UUID):
        job=ServiceJob.objects.select_related("request","accepted_quote").filter(id=job_id,request__customer=request.auth).first()
        if not job:
            reject(404, "NOT_FOUND", "Job not found")
        return {"data":{"id":str(job.id),"state":job.state,"version":job.version,
               "vehicle_snapshot":job.request.vehicle_snapshot,"address_snapshot":job.request.address_snapshot,
               "quoted_total_paise":job.accepted_quote.total_paise}}
