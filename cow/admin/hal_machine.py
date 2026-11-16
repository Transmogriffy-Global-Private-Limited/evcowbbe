"""Admin operational machine gateway; customer/driver tokens never authorize HAL."""
from uuid import UUID

from ninja import Query, Schema
from pydantic import ConfigDict, Field

from cow.admin.controls import _json, _key
from cow.hal import v1_machine as machine
from cow.models import HALV1FactReceipt, HALV1TraceReceipt, HALV1ChargerOperation
from cow.services import rbac


class Strict(Schema):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class Empty(Strict):
    pass


class OperationInput(Strict):
    kind: str=Field(min_length=4,max_length=32)
    connector_id: UUID | None = None
    parameters: dict = Field(default_factory=dict)
    reason: str = Field(min_length=8,max_length=500)


class MappingData(Strict):
    charger_id: UUID
    state: str
    already_current: bool


class MappingView(Strict):
    charger_id: UUID
    ocpp_identity: str
    configured: bool
    state: str
    synchronized_at: str | None
    failure_code: str
    inventory_matches: bool


class OperationView(Strict):
    id: UUID
    charger_id: UUID
    connector_id: UUID | None
    kind: str
    state: str
    hal_operation_id: UUID | None
    failure_code: str
    provider_state: str
    provider_ocpp_result: str
    result_summary: dict
    attempted_at: str | None
    resolved_at: str | None
    resolution_ref: str


class ResolveInput(Strict):
    reason: str = Field(min_length=8,max_length=500)
    evidence_ref: str = Field(min_length=8,max_length=256)


class ReasonInput(Strict):
    reason: str = Field(min_length=8,max_length=500)


class RuntimeCharger(Strict):
    charger_id: UUID
    connection_state: str
    fresh: bool
    observed_at: str | None
    sequence: int | None = None


class RuntimeConnector(Strict):
    connector_id: UUID
    status: str
    fresh: bool
    sequence: int | None = None
    observed_at: str | None = None


class MappingEnvelope(Strict):data:MappingView
class MappingActionEnvelope(Strict):data:MappingData
class OperationEnvelope(Strict):data:OperationView
class ChargerRuntimeEnvelope(Strict):data:RuntimeCharger
class ConnectorRuntimeEnvelope(Strict):data:RuntimeConnector


class FactView(Strict):
    fact_id: UUID
    charger_id: UUID
    fact_type: str
    processing_state: str
    occurred_at: str


class FactPage(Strict):
    items:list[FactView]


class FactPageEnvelope(Strict):data:FactPage


class OperationPage(Strict):
    items: list[OperationView]


class OperationPageEnvelope(Strict):
    data: OperationPage


class TraceView(Strict):
    event_id: UUID
    category: str
    source: str
    target: str
    phase: str
    summary: str
    occurred_at: str


class TracePage(Strict):
    items: list[TraceView]


class TracePageEnvelope(Strict):
    data: TracePage


def register(router):
    @router.get('/hal-v1/chargers/{charger_id}/mapping',
                operation_id='cowHALV1MappingGet',response=MappingEnvelope)
    def mapping_get(request,charger_id:UUID):
        return {'data':machine.mapping_read(request.auth,charger_id)}

    @router.post('/hal-v1/chargers/{charger_id}/mapping/sync',
                 operation_id='cowHALV1MappingSync',response=MappingActionEnvelope)
    def mapping_sync(request,charger_id:UUID,payload:Empty):
        _json(request);_key(request)
        state=machine.prepare_mapping(request.auth,charger_id)
        if state['state']=='PENDING':
            state['state']=machine.send_mapping_once(request.auth,charger_id)
        return {'data':state}

    @router.post('/hal-v1/chargers/{charger_id}/mapping/reconcile',
                 operation_id='cowHALV1MappingReconcile',response=MappingActionEnvelope)
    def mapping_reconcile(request,charger_id:UUID,payload:Empty):
        _json(request);_key(request)
        return {'data':machine.reconcile_mapping(request.auth,charger_id)}

    @router.get('/hal-v1/chargers/{charger_id}/runtime',
                operation_id='cowHALV1ChargerRuntime',response=ChargerRuntimeEnvelope)
    def charger_runtime(request,charger_id:UUID):
        return {'data':machine.charger_runtime(request.auth,charger_id)}

    @router.get('/hal-v1/connectors/{connector_id}/runtime',
                operation_id='cowHALV1ConnectorRuntime',response=ConnectorRuntimeEnvelope)
    def connector_runtime(request,connector_id:UUID):
        return {'data':machine.connector_runtime(request.auth,connector_id)}

    @router.post('/hal-v1/chargers/{charger_id}/operations',
                 operation_id='cowHALV1OperationCreate',response={201:OperationEnvelope})
    def new_operation(request,charger_id:UUID,payload:OperationInput):
        _json(request)
        return 201,{'data':machine.prepare_operation(request.auth,charger_id,
            payload.connector_id,payload.kind,payload.parameters,_key(request),payload.reason)}

    @router.get('/hal-v1/operations',operation_id='cowHALV1OperationsList',
                response=OperationPageEnvelope)
    def operations(request,limit:int=Query(50,ge=1,le=100)):
        rbac.require(request.auth,'charging.read')
        qs=HALV1ChargerOperation.objects.filter(scope_id=machine.scope()).order_by('-created_at','-id')[:limit]
        return {'data':{'items':[machine.operation_view(x) for x in qs]}}

    @router.get('/hal-v1/operations/{operation_id}',
                operation_id='cowHALV1OperationGet',response=OperationEnvelope)
    def read_operation(request,operation_id:UUID):
        return {'data':machine.read_operation(request.auth,operation_id)}

    @router.post('/hal-v1/operations/{operation_id}/dispatch',
                 operation_id='cowHALV1OperationDispatch',response=OperationEnvelope)
    def dispatch(request,operation_id:UUID,payload:Empty):
        _json(request);_key(request)
        rbac.require(request.auth,'charging.command')
        machine.send_operation_once(request.auth,operation_id)
        return {'data':machine.read_operation(request.auth,operation_id)}

    @router.post('/hal-v1/operations/{operation_id}/reconcile',
                 operation_id='cowHALV1OperationReconcile',response=OperationEnvelope)
    def reconcile(request,operation_id:UUID,payload:Empty):
        _json(request);_key(request)
        return {'data':machine.reconcile_operation(request.auth,operation_id)}

    @router.post('/hal-v1/operations/{operation_id}/cancel-unsent',
                 operation_id='cowHALV1OperationCancelUnsent',response=OperationEnvelope)
    def cancel_unsent(request,operation_id:UUID,payload:ReasonInput):
        _json(request);_key(request)
        return {'data':machine.cancel_unsent_operation(request.auth,operation_id,payload.reason)}

    @router.post('/hal-v1/operations/{operation_id}/resolve',
                 operation_id='cowHALV1OperationResolve',response=OperationEnvelope)
    def resolve(request,operation_id:UUID,payload:ResolveInput):
        _json(request);_key(request)
        return {'data':machine.resolve_operation(request.auth,operation_id,payload.reason,payload.evidence_ref)}

    @router.get('/hal-v1/chargers/{charger_id}/trace-events',
                operation_id='cowHALV1TraceList',response=TracePageEnvelope)
    def traces(request,charger_id:UUID,limit:int=Query(50,ge=1,le=100)):
        rbac.require(request.auth,'charging.trace.read')
        charger=machine.inventory(charger_id)
        qs=HALV1TraceReceipt.objects.filter(
            charger_id=charger.pk,scope_id=machine.scope(),
        ).order_by('-received_at','-event_id')[:limit]
        return {'data':{'items':[{
            'event_id':str(x.event_id),'category':x.payload.get('category',''),
            'source':x.payload.get('source',''),'target':x.payload.get('target',''),
            'phase':x.payload.get('phase',''),'summary':x.payload.get('summary',''),
            'occurred_at':x.payload.get('occurred_at',''),
        } for x in qs]}}

    @router.get('/hal-v1/chargers/{charger_id}/facts',
                operation_id='cowHALV1FactList',response=FactPageEnvelope)
    def facts(request,charger_id:UUID,limit:int=Query(50,ge=1,le=100)):
        rbac.require(request.auth,'charging.read')
        machine.inventory(charger_id)
        qs=HALV1FactReceipt.objects.filter(charger_id=charger_id,
                 scope_id=machine.scope()).order_by('-received_at','-fact_id')[:limit]
        return {'data':{'items':[{'fact_id':str(x.fact_id),'charger_id':str(x.charger_id),
            'fact_type':x.fact_type,'processing_state':x.processing_state,
            'occurred_at':x.occurred_at.isoformat()} for x in qs]}}
