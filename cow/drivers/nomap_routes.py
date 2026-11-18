"""Driver-facing, frontend-independent maps-free operations, versioned OpenAPI."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from ninja import Query, Schema
from pydantic import ConfigDict, Field
from cow.common.nomap_wire import (
    JobPageEnvelope, JobEnvelope, LocationEnvelope, VerifyOTPEnvelope, DriverClosureEnvelope,
)
from cow.services import idempotency
from cow.services import nomap_location as loc
from cow.services import nomap_job_ops as ops
from cow.services import booking_trust_nomap as handover


class Strict(Schema):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class LocationSample(Strict):
    latitude: str
    longitude: str
    accuracy_m: int=Field(ge=1,le=200)
    observed_at: datetime
    sequence: int=Field(ge=1)
    client_event_id: UUID

class ExpectedVersion(Strict):
    expected_version: int=Field(ge=1)

class VerifyOTP(Strict):
    ticket_id: UUID
    otp: str=Field(pattern=r'^\d{6}$')
    connector_id: UUID

class Completion(ExpectedVersion):
    driver_ref: str=Field(min_length=12,max_length=160)


def _mutation(request,scope,payload,op):
    return idempotency.execute(request.auth,scope,request.headers.get('Idempotency-Key'),
        payload.model_dump(mode='json'),lambda:(200,{'data':op()}),retention_hours=168)[1]


def register(router):
    @router.get('/jobs',response=JobPageEnvelope,operation_id='cowV3DriverJobsList')
    def jobs(request,limit:int=Query(50,ge=1,le=100)):
        return {'data':ops.list_driver_jobs(request.auth,limit)}

    @router.get('/jobs/{job_id}',response=JobEnvelope,operation_id='cowV3DriverJobGet')
    def job(request,job_id:UUID):
        return {'data':ops.get_driver_job(request.auth,job_id)}

    @router.post('/jobs/{job_id}/location',response=LocationEnvelope,operation_id='cowV3DriverLocationSubmit')
    def observe(request,job_id:UUID,payload:LocationSample):
        return {'data':loc.ingest(actor=request.auth,job_id=job_id,**payload.model_dump())}

    @router.post('/jobs/{job_id}/accept',response=JobEnvelope,operation_id='cowV3DriverJobAccept')
    def accept(request,job_id:UUID,payload:ExpectedVersion):
        return _mutation(request,f'driver:v3:job:{job_id}:accept',payload,
            lambda:ops.driver_transition(actor=request.auth,job_id=job_id,
                 version=payload.expected_version,target='ACCEPTED'))

    @router.post('/jobs/{job_id}/depart',response=JobEnvelope,operation_id='cowV3DriverJobDepart')
    def depart(request,job_id:UUID,payload:ExpectedVersion):
        return _mutation(request,f'driver:v3:job:{job_id}:depart',payload,
            lambda:ops.driver_transition(actor=request.auth,job_id=job_id,
                 version=payload.expected_version,target='EN_ROUTE'))

    @router.post('/jobs/{job_id}/arrive',response=JobEnvelope,operation_id='cowV3DriverGeofenceArrival')
    def arrive(request,job_id:UUID,payload:ExpectedVersion):
        return _mutation(request,f'driver:v3:job:{job_id}:arrive',payload,
            lambda:ops.driver_transition(actor=request.auth,job_id=job_id,
                 version=payload.expected_version,target='ARRIVED'))

    @router.post('/handover/verify-otp',response=VerifyOTPEnvelope,operation_id='cowV3DriverHandoverVerifyOTP')
    def otp(request,payload:VerifyOTP):
        return _mutation(request,f'driver:v3:handover:{payload.ticket_id}',payload,
            lambda:handover.driver_verify_otp(actor=request.auth,
                ticket_id=payload.ticket_id,otp=payload.otp,connector_id=payload.connector_id))

    @router.post('/jobs/{job_id}/completion',response=DriverClosureEnvelope,operation_id='cowV3DriverCompletionSubmit')
    def completion(request,job_id:UUID,payload:Completion):
        return _mutation(request,f'driver:v3:job:{job_id}:complete',payload,
            lambda:ops.driver_submit_completion(actor=request.auth,job_id=job_id,
                  version=payload.expected_version,driver_ref=payload.driver_ref))
