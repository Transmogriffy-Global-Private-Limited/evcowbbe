"""Owner-only CoW v1 HAL charging projection. No client calls to Go HAL."""
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from cow.hal import v1_charging
from cow.services import booking_commercial
from cow.services.booking_providers import trusted_provider
from django.utils import timezone
from cow.common.errors import reject


class Strict(Schema):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class StageStartInput(Strict):
    permit_id: UUID


class StageStopInput(Strict):
    reason: str = Field(default='user_requested', min_length=1, max_length=100)


def _idem(request):
    key=request.headers.get('Idempotency-Key','')
    if not isinstance(key,str) or not 16<=len(key)<=128 or '\r' in key or '\n' in key:
        reject(428,'HAL_CHARGE_IDEMPOTENCY_REQUIRED','Bounded Idempotency-Key required')
    return key


class BookingAcceptInput(Strict):
    request_id: UUID
    acceptance_token: str = Field(min_length=20,max_length=250)


class BookingStartInput(Strict):
    handover_token: str = Field(min_length=20,max_length=250)


def register_hal_v1_charging_routes(router):
    @router.post('/charging-start-intents',response={202:dict},
                 operation_id='cowHALV1CustomerStartIntentStage')
    def stage(request,payload:StageStartInput):
        # A permit is *server-owned* and must be issued by a prior committed
        # commercial/operational flow; this route cannot mint one.
        return 202,{'data':v1_charging.stage_start(request.auth,payload.permit_id,_idem(request))}

    @router.get('/charging-start-intents/{intent_id}',response=dict,
                operation_id='cowHALV1CustomerStartIntentGet')
    def progress(request,intent_id:UUID):
        return {'data':v1_charging.owner_intent(request.auth,intent_id)}

    @router.get('/charging-sessions/{session_id}',response=dict,
                operation_id='cowHALV1CustomerSessionGet')
    def session(request,session_id:UUID):
        return {'data':v1_charging.owner_session(request.auth,session_id)}

    @router.post('/charging-sessions/{session_id}/stop',response={202:dict},
                 operation_id='cowHALV1CustomerStopStage')
    def stop(request,session_id:UUID,payload:StageStopInput):
        return 202,{'data':v1_charging.stage_stop(
            request.auth,session_id,_idem(request),reason=payload.reason)}

    # BOOKING COMMERCIAL V2 - provider returns already server-verified typed
    # proof objects; neither request body may assert OTP or location verified.
    @router.post('/commercial/bookings/accept',response={201:dict},
                 operation_id='cowCommercialBookingAccept')
    def accept_booking(request,payload:BookingAcceptInput):
        provider=trusted_provider()
        proof=provider.resolve_acceptance(request.auth,payload.request_id,
                                          payload.acceptance_token,timezone.now())
        return 201,{'data':booking_commercial.accept_booking(
            owner=request.auth,proof=proof,request_key=_idem(request),
            verify_acceptance=provider.verify_acceptance)}

    @router.get('/commercial/bookings/{booking_id}',response=dict,
                operation_id='cowCommercialBookingGet')
    def booking_details(request,booking_id:UUID):
        return {'data':booking_commercial.booking_financial_view(request.auth,booking_id)}

    @router.post('/commercial/bookings/{booking_id}/charge',response={202:dict},
                 operation_id='cowCommercialBookingChargeStage')
    def start_booking_charge(request,booking_id:UUID,payload:BookingStartInput):
        provider=trusted_provider()
        proof=provider.resolve_handover(request.auth,booking_id,
                                        payload.handover_token,timezone.now())
        return 202,{'data':booking_commercial.prepare_and_stage_start(
            owner=request.auth,booking_id=booking_id,proof=proof,
            start_key=_idem(request),verify_handover=provider.verify_handover)}

    @router.get('/commercial/bookings/{booking_id}/statement',response=dict,
                operation_id='cowCommercialBookingStatement')
    def booking_statement(request,booking_id:UUID):
        return {'data':booking_commercial.settled_booking_statement(request.auth,booking_id)}
