"""Admin charge-machine read/reconciliation; never a customer command bypass."""
from uuid import UUID
from ninja import Schema
from pydantic import ConfigDict, Field
from cow.hal import v1_charging
from cow.services import booking_schedule_admin, booking_commercial
from cow.services.booking_providers import trusted_provider
from cow.models import HALV1ChargingCommand, HALV1ChargingSession
from cow.services import rbac
from cow.common.errors import reject


class AdminStopPayload(Schema):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(default='admin_requested', min_length=1, max_length=100)

class MinimumDistanceTier(Schema):
    model_config=ConfigDict(extra='forbid')
    up_to_m: int | None = Field(default=None,ge=1)
    min_gross_paise: int = Field(gt=0)


class MinimumSchedulePayload(Schema):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    inside_slabs: list[MinimumDistanceTier] = Field(min_length=1,max_length=64)
    outside_slabs: list[MinimumDistanceTier] | None = None
    authorization_ref: str = Field(min_length=12,max_length=200)


class CompleteBookingPayload(Schema):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    completion_ref: str = Field(min_length=12,max_length=200)


def register_hal_v1_charging_routes(router):
    @router.get('/hal-v1/charge-commands/{command_id}',response=dict,
                operation_id='cowHALV1ChargeCommandGet')
    def command(request,command_id:UUID):
        rbac.require(request.auth,'charging.read')
        row=HALV1ChargingCommand.objects.filter(pk=command_id).first()
        if not row:reject(404,'HAL_CHARGE_COMMAND_NOT_FOUND','Command not found')
        return {'data':v1_charging.command_view(row)}

    @router.post('/hal-v1/charge-commands/{command_id}/reconcile',response=dict,
                 operation_id='cowHALV1ChargeCommandReconcile')
    def reconcile(request,command_id:UUID):
        return {'data':v1_charging.reconcile_command(command_id,actor=request.auth)}

    @router.get('/hal-v1/charging-sessions/{session_id}',response=dict,
                operation_id='cowHALV1ChargingSessionGet')
    def get_session(request,session_id:UUID):
        rbac.require(request.auth,'charging.read')
        row=HALV1ChargingSession.objects.select_related('intent__permit').filter(pk=session_id).first()
        if not row:reject(404,'HAL_CHARGE_SESSION_NOT_FOUND','Session not found')
        return {'data':v1_charging.session_view(row)}


    @router.post('/hal-v1/charging-sessions/{session_id}/stop',response={202:dict},
                 operation_id='cowHALV1AdminChargeStopStage')
    def stop_session(request,session_id:UUID, payload:AdminStopPayload):
        rbac.require(request.auth,'charging.command')
        key=request.headers.get('Idempotency-Key','')
        return 202,{'data':v1_charging.stage_stop(None,session_id,key,
            initiated_by='admin',reason=payload.reason,actor=request.auth)}


    @router.post('/hal-v1/charging-start-intents/{intent_id}/inspect',response=dict,
                 operation_id='cowHALV1AdminIntentInspect')
    def inspect_intent(request,intent_id:UUID):
        return {'data':v1_charging.reconcile_intent(intent_id,actor=request.auth)}

    @router.post('/hal-v1/commercial/tariffs/{policy_id}/minimum',response=dict,
                 operation_id='cowHALV1CommercialMinimumConfigure')
    def put_minimum(request,policy_id:UUID,payload:MinimumSchedulePayload):
        return {'data':booking_schedule_admin.configure(
            actor=request.auth,policy_id=policy_id,
            inside_slabs=[x.model_dump() for x in payload.inside_slabs],
            outside_slabs=[x.model_dump() for x in payload.outside_slabs]
                if payload.outside_slabs is not None else None,
            authorization_ref=payload.authorization_ref)}

    @router.get('/hal-v1/commercial/tariffs/{policy_id}/minimum',response=dict,
                operation_id='cowHALV1CommercialMinimumGet')
    def get_minimum(request,policy_id:UUID):
        return {'data':booking_schedule_admin.view(request.auth,policy_id)}

    @router.get('/hal-v1/commercial/bookings/{booking_id}',response=dict,
                operation_id='cowHALV1AdminBookingFinancialGet')
    def admin_booking(request,booking_id:UUID):
        rbac.require(request.auth,'payments.read')
        from cow.models import CoWBookingCommercialCommitment
        b=CoWBookingCommercialCommitment.objects.filter(pk=booking_id).first()
        if b is None:reject(404,'BOOKING_NOT_FOUND','Booking not found')
        return {'data':booking_commercial.booking_view(b)}

    @router.post('/hal-v1/commercial/bookings/{booking_id}/settle',response=dict,
                 operation_id='cowHALV1BookingSettle')
    def admin_book_settle(request,booking_id:UUID,payload:CompleteBookingPayload):
        provider=trusted_provider()
        return {'data':booking_commercial.settle_completed_booking(
            booking_id=booking_id,verify_driver_completion=provider.verify_driver_completion,
            completion_ref=payload.completion_ref,actor=request.auth)}
