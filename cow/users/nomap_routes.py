"""Customer-side backend contracts for independent junior frontend integration."""
from datetime import datetime
from uuid import UUID
from ninja import Schema
from pydantic import ConfigDict, Field
from cow.common.nomap_wire import (
    PreviewEnvelope, LocationEnvelope, TrackingEnvelope, OTPEnvelope, ConsentEnvelope,
)
from cow.services import idempotency
from cow.services import nomap_location as location
from cow.services import booking_trust_nomap as handover

class Strict(Schema):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class Preview(Strict):
    request_id: UUID
    zone_id: UUID | None=None

class LocationSample(Strict):
    latitude: str
    longitude: str
    accuracy_m: int=Field(ge=1,le=200)
    observed_at: datetime
    sequence: int=Field(ge=1)
    client_event_id: UUID

class Blank(Strict):
    pass


def _mutation(request,scope,payload,op):
    return idempotency.execute(request.auth,scope,request.headers.get('Idempotency-Key'),
        payload.model_dump(mode='json'),lambda:(200,{'data':op()}),retention_hours=168)[1]


def register(router):
    @router.post('/commercial/no-maps/preview',response=PreviewEnvelope,operation_id='cowV3CustomerNoMapsPreview')
    def preview(request,payload:Preview):
        return _mutation(request,f'cow:v3:preview:{payload.request_id}',payload,
            lambda:handover.preview_acceptance(owner=request.auth,
                    request_id=payload.request_id,zone_id=payload.zone_id))

    @router.post('/jobs/{job_id}/location',response=LocationEnvelope,operation_id='cowV3CustomerLocationSubmit')
    def observe(request,job_id:UUID,payload:LocationSample):
        return {'data':location.ingest(actor=request.auth,job_id=job_id,
                                      **payload.model_dump())}

    @router.get('/jobs/{job_id}/driver-tracking',response=TrackingEnvelope,
                operation_id='cowV3CustomerDriverTracking')
    def tracking(request,job_id:UUID):
        return {'data':location.tracking_for_customer(request.auth,job_id)}

    @router.post('/commercial/bookings/{booking_id}/handover/otp',response=OTPEnvelope,
                 operation_id='cowV3CustomerIssueHandoverOTP')
    def issue_otp(request,booking_id:UUID,payload:Blank):
        # OTP is only returned for a freshly minted issuance. HTTP retries
        # return no OTP and must not recreate a different credential.
        return {'data':handover.issue_otp(owner=request.auth,booking_id=booking_id,
                     issue_key=request.headers.get('Idempotency-Key',''))}

    @router.post('/commercial/handover/{ticket_id}/customer-consent',response=ConsentEnvelope,
                 operation_id='cowV3CustomerHandoverConsent')
    def customer_consent(request,ticket_id:UUID,payload:Blank):
        return {'data':handover.customer_consent(owner=request.auth,ticket_id=ticket_id)}
