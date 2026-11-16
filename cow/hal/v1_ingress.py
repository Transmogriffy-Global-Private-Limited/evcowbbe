"""Independent authenticated Go HAL v1 fact and diagnostic trace receivers.

Durable immutable receipts are the authority for later projection/replay. This
slice projects only charger/connector operational observations; transaction
and command facts remain durably PENDING_BUSINESS for future ChargingIntent
materialization. No billing, wallet, job or physical command is inferred here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone as dt_timezone
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from cow.common.errors import reject
from cow.hal.v1_wire import uid, WireError
from cow.models import (Charger, ChargerConnector, HALV1Mapping,
                        HALV1FactReceipt, HALV1ChargerRuntime,
                        HALV1ConnectorRuntime, HALV1TraceReceipt)
from cow.services import operational_eligibility as safety

FACT_TYPES=frozenset({
    'charger.connection.updated','connector.status.updated','command.updated',
    'transaction.started','transaction.meter','transaction.soc','transaction.completed',
})


def _auth(request, secret):
    header=request.headers.get('Authorization','')
    if (not secret or len(secret)<32 or not header.startswith('Bearer ') or
            not hmac.compare_digest(header[7:],secret)):
        reject(401,'HAL_V1_SERVICE_AUTH_REQUIRED','HAL v1 service authentication required')


def _decode(request):
    if request.content_type.split(';')[0].lower()!='application/json':
        reject(415,'HAL_V1_MEDIA_TYPE','JSON expected')
    raw=request.body
    if not raw or len(raw)>32768:
        reject(400,'HAL_V1_BODY_INVALID','HAL event exceeds allowed size or is empty')
    def no_dupes(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('duplicate JSON property')
            result[k]=v
        return result
    try:
        event=json.loads(raw,object_pairs_hook=no_dupes,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError,UnicodeDecodeError):
        reject(400,'HAL_V1_BODY_INVALID','Malformed HAL event')
    if not isinstance(event,dict):
        reject(400,'HAL_V1_BODY_INVALID','Expected HAL object')
    return event


def _uuid(raw,label):
    try:return uid(raw)
    except WireError:reject(422,'HAL_V1_ID_INVALID',label+' requires canonical UUID')


def _time(raw):
    try:
        value=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if value.tzinfo is None or value.utcoffset() is None:raise ValueError()
        return value.astimezone(dt_timezone.utc)
    except (AttributeError,TypeError,ValueError):
        reject(400,'HAL_V1_TIME_INVALID','Invalid occurred_at')


def _digest(envelope):
    try:
        import rfc8785
        return hashlib.sha256(rfc8785.dumps({k:v for k,v in envelope.items() if k!='immutable_content_sha256'})).hexdigest()
    except ImportError:
        reject(503,'HAL_V1_JCS_UNAVAILABLE','RFC 8785 canonicalizer not installed')
    except (ValueError,TypeError,OverflowError):
        reject(400,'HAL_V1_JCS_INVALID','Invalid canonical fact payload')


def _verify(envelope):
    supplied=envelope.get('immutable_content_sha256')
    if not isinstance(supplied,str) or not re.fullmatch(r'[0-9a-f]{64}',supplied):
        reject(400,'HAL_V1_DIGEST_INVALID','Missing immutable content digest')
    digest=_digest(envelope)
    if not hmac.compare_digest(digest,supplied):
        reject(409,'HAL_V1_DIGEST_MISMATCH','HAL immutable fact integrity failure')
    return digest


def _mapping(payload):
    """Require exact Go fact ownership and *current* CoW inventory parity."""
    from cow.hal.v1_machine import mapping_payload, scope
    scope_id=scope()
    if _uuid(payload.get('cpo_id'),'cpo_id')!=scope_id:
        reject(403,'HAL_V1_FOREIGN_SCOPE','Fact belongs to another HAL scope')
    charger_id=_uuid(payload.get('cms_charger_id'),'cms_charger_id')
    identity=payload.get('charger_ocpp_identity')
    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,150}',identity):
        reject(422,'HAL_V1_CHARGER_ID_MISSING','Fact lacks exact OCPP identity')
    m=HALV1Mapping.objects.select_related('charger').filter(
        scope_id=scope_id,state='SYNCHRONIZED',charger_id=charger_id,
        ocpp_identity=identity).first()
    if not m:
        reject(422,'HAL_V1_CHARGER_NOT_OWNED','Fact charger not enrolled in CoW HAL scope')
    _mapping_inventory_parity(m,identity)
    return m


def _mapping_inventory_parity(mapping, identity):
    """Reject an old synchronized snapshot after inventory/connector drift."""
    from cow.hal.v1_machine import mapping_payload
    charger=mapping.charger
    if (mapping.ocpp_identity != identity or
            charger.hal_identity != identity or
            mapping.snapshot != mapping_payload(charger)):
        reject(409,'HAL_V1_MAPPING_DRIFT','HAL mapping no longer matches CoW inventory')


def _trace_mapping(identity):
    """Go trace v1 has OCPP identity and CPO, but no CMS charger UUID."""
    from cow.hal.v1_machine import scope
    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,150}',identity):
        reject(422,'HAL_V1_CHARGER_ID_MISSING','Trace lacks exact OCPP identity')
    m=HALV1Mapping.objects.select_related('charger').filter(
        scope_id=scope(),ocpp_identity=identity,state='SYNCHRONIZED').first()
    if not m:
        reject(422,'HAL_V1_CHARGER_NOT_OWNED','Trace charger not CoW enrolled')
    _mapping_inventory_parity(m,identity)
    return m


def _project(fact_type,payload,mapping):
    if fact_type=='charger.connection.updated':
        state=payload.get('connection_state')
        sequence=payload.get('connection_sequence')
        generation=payload.get('connection_generation')
        observed=_time(payload.get('observed_at'))
        if state not in {'ONLINE','OFFLINE','UNKNOWN'} or type(sequence) is not int or sequence<=0 or type(generation) is not int or generation<0:
            reject(422,'HAL_V1_CONNECTION_FACT_INVALID','Invalid connection observation')
        row,_=HALV1ChargerRuntime.objects.select_for_update().get_or_create(charger=mapping.charger)
        if sequence>row.sequence:
            row.connection_state=state;row.sequence=sequence;row.generation=generation
            row.observed_at=observed;row.save()
        return 'APPLIED'
    if fact_type=='connector.status.updated':
        connector_id=_uuid(payload.get('cms_connector_id'),'cms_connector_id')
        connector=ChargerConnector.objects.filter(pk=connector_id,charger=mapping.charger).first()
        number=payload.get('ocpp_connector_number')
        sequence=payload.get('connector_status_sequence')
        state=payload.get('ocpp_connector_status')
        observed=_time(payload.get('observed_at'))
        if (connector is None or connector.connector_number!=number or
                type(sequence) is not int or sequence<=0 or not isinstance(state,str) or not 1<=len(state)<=40):
            reject(422,'HAL_V1_CONNECTOR_FACT_INVALID','Connector ownership or sequence invalid')
        row,_=HALV1ConnectorRuntime.objects.select_for_update().get_or_create(connector=connector)
        if sequence>row.sequence:
            row.ocpp_status=state;row.sequence=sequence;row.observed_at=observed
            row.save()
        return 'APPLIED'
    # Store but do not interpret these as charging session or financial truth.
    return 'PENDING_BUSINESS'


def ingest_fact(request):
    _auth(request,settings.COW_HAL_V1_FACT_BEARER_TOKEN)
    e=_decode(request)
    required={'fact_id','fact_type','schema_version','occurred_at','producer',
              'immutable_content_sha256','payload'}
    if set(e)!=required or e.get('producer')!='ocpp-hal-go-new' or e.get('schema_version')!=1:
        reject(400,'HAL_V1_ENVELOPE_INVALID','HAL fact envelope invalid')
    fact_id=_uuid(e.get('fact_id'),'fact_id')
    if request.headers.get('Idempotency-Key','')!=fact_id:
        reject(400,'HAL_V1_IDEMPOTENCY_MISMATCH','Idempotency-Key must match fact_id')
    if e.get('fact_type') not in FACT_TYPES or not isinstance(e.get('payload'),dict):
        reject(422,'HAL_V1_FACT_UNSUPPORTED','Unsupported HAL fact type')
    at=_time(e['occurred_at'])
    digest=_verify(e)
    with transaction.atomic():
        safety.lock_gate()
        prior=HALV1FactReceipt.objects.select_for_update().filter(fact_id=fact_id).first()
        if prior:
            if prior.digest!=digest:
                reject(409,'HAL_V1_FACT_ID_REUSE','Fact ID reused with changed content')
            return
        mapping=_mapping(e['payload'])
        state=_project(e['fact_type'],e['payload'],mapping)
        HALV1FactReceipt.objects.create(
            fact_id=UUID(fact_id),scope_id=mapping.scope_id,charger=mapping.charger,
            fact_type=e['fact_type'],digest=digest,occurred_at=at,
            payload=e['payload'],processing_state=state)


def ingest_trace(request):
    _auth(request,settings.COW_HAL_V1_TRACE_BEARER_TOKEN)
    e=_decode(request)
    required={'schema_version','trace_id','event_id','cpo_id','cms_start_intent_id',
              'cms_charging_session_id','cms_command_id','cms_charger_operation_id',
              'hal_charger_operation_id','hal_transaction_id','ocpp_transaction_id',
              'charger_ocpp_identity','ocpp_connector_number','source','target',
              'category','protocol','phase','summary','occurred_at','state_before',
              'state_after','correlation_id','data','immutable_content_sha256'}
    if set(e)!=required or e.get('schema_version')!=1:
        reject(400,'HAL_V1_TRACE_INVALID','Trace envelope invalid')
    event_id=_uuid(e.get('event_id'),'event_id')
    from cow.hal.v1_machine import scope
    if _uuid(e.get('cpo_id'),'cpo_id')!=scope():
        reject(403,'HAL_V1_FOREIGN_SCOPE','Trace belongs to another HAL instance')
    if request.headers.get('Idempotency-Key','')!=event_id:
        reject(400,'HAL_V1_TRACE_IDEMPOTENCY','Trace idempotency mismatch')
    _uuid(e.get('trace_id'),'trace_id');_time(e.get('occurred_at'))
    if not isinstance(e.get('data'),dict):
        reject(400,'HAL_V1_TRACE_INVALID','Trace data invalid')
    digest=_verify(e)
    with transaction.atomic():
        safety.lock_gate()
        old=HALV1TraceReceipt.objects.select_for_update().filter(event_id=event_id).first()
        if old:
            if old.digest!=digest:reject(409,'HAL_V1_TRACE_CONFLICT','Trace identity conflict')
            return
        mapping=_trace_mapping(e.get('charger_ocpp_identity'))
        # Only sanitized diagnostic payloads; no credentials or raw OCPP frames.
        HALV1TraceReceipt.objects.create(event_id=UUID(event_id),scope_id=mapping.scope_id,
            charger=mapping.charger,digest=digest,
            event_type=str(e.get('category'))[:80],payload={
                'trace_id':e['trace_id'],'charger_ocpp_identity':e['charger_ocpp_identity'],
                'source':e['source'],'target':e['target'],'category':e['category'],
                'protocol':e['protocol'],'phase':e['phase'],
                'summary':str(e['summary'])[:250],
                'occurred_at':e['occurred_at']})
