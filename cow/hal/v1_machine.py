"""CoW-owned, durable Go HAL v1 operations. No customer start/stop authority.

All physical OCPP admin operations require an explicit switch and a committed
command receipt. A marked attempted operation is never silently redispatched.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from cow.common.errors import reject
from cow.models import (AuditEvent, Charger, ChargerConnector,
                        HALV1Mapping, HALV1ChargerOperation,
                        HALV1ChargerRuntime, HALV1ConnectorRuntime)
from cow.services import operational_eligibility as safety, rbac
from cow.hal.v1_wire import V1Client, WireError, uid


def scope() -> str:
    try:
        return uid(settings.COW_HAL_V1_SCOPE_ID)
    except (WireError, AttributeError) as exc:
        reject(503, 'HAL_SCOPE_NOT_CONFIGURED', 'CoW HAL scope not configured')
        raise AssertionError from exc


def client() -> V1Client:
    try:
        return V1Client(settings.COW_HAL_V1_BASE_URL,
                        settings.COW_HAL_V1_CMS_BEARER_TOKEN)
    except (WireError, AttributeError):
        reject(503, 'HAL_V1_UNAVAILABLE', 'Dedicated CoW HAL not configured')


def _enabled(setting, label):
    if not getattr(settings, setting, False):
        reject(409, 'HAL_V1_DISABLED', label + ' has not been enabled')


def _audit(actor, ident, action, **details):
    AuditEvent.objects.create(actor=actor, target_type='HALV1Machine',
                              target_id=ident, action=action, details=details)


def inventory(charger_id, *, lock=False):
    q = Charger.objects.select_for_update() if lock else Charger.objects
    charger = q.filter(pk=charger_id).first()
    if charger is None:
        reject(404,'HAL_CHARGER_NOT_FOUND','Charger is not in CoW inventory')
    if not isinstance(charger.hal_identity,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,150}',charger.hal_identity):
        reject(409,'HAL_IDENTITY_INVALID','Charger OCPP identity must be a safe protocol path identity')
    if charger.serial_number is not None and (len(charger.serial_number)>100 or
            any(not ('!' <= char <= '~') or char=='/' for char in charger.serial_number)):
        reject(409,'HAL_SERIAL_INVALID','Charger serial identity evidence is invalid')
    return charger


def mapping_payload(charger):
    connectors = list(ChargerConnector.objects.filter(charger=charger).order_by('connector_number','id'))
    if not connectors or any(x.connector_number<=0 for x in connectors):
        reject(409,'HAL_CONNECTORS_INCOMPLETE','Charger has no valid connector mapping')
    return {
        'cpo_id':scope(),
        'cms_charger_id':str(charger.pk),
        'charger_ocpp_identity':charger.hal_identity,
        'expected_serial':charger.serial_number or '',
        'enabled':bool(charger.active and charger.operational_status=='AVAILABLE'),
        'connectors':[{'cms_connector_id':str(x.pk),
                       'ocpp_connector_number':x.connector_number} for x in connectors],
    }


def mapping_read(actor, charger_id):
    rbac.require(actor,'fleet.read')
    c=inventory(charger_id)
    m=HALV1Mapping.objects.filter(charger=c,scope_id=scope()).first()
    return {'charger_id':str(c.pk),'ocpp_identity':c.hal_identity,
            'configured':m is not None,'state':m.state if m else 'NOT_REGISTERED',
            'synchronized_at':m.synchronized_at.isoformat() if m and m.synchronized_at else None,
            'failure_code':m.failure_code if m else '',
            'inventory_matches':m.snapshot==mapping_payload(c) if m else False}


def prepare_mapping(actor, charger_id):
    _enabled('COW_HAL_V1_MAPPING_SYNC_ENABLED','HAL mapping sync')
    with transaction.atomic():
        actor = safety.fresh_admin(actor,'fleet.manage')
        safety.lock_gate()
        c=inventory(charger_id,lock=True)
        safety.ensure_quiescent(charger_id=c.pk)
        snapshot=mapping_payload(c)
        mapping=HALV1Mapping.objects.select_for_update().filter(charger=c).first()
        if mapping is not None and mapping.scope_id!=UUID(scope()):
            reject(409,'HAL_SCOPE_CONFLICT','Charger registered to a different HAL scope')
        if mapping is not None and mapping.state=='ATTEMPTED':
            reject(409,'HAL_MAPPING_OUTCOME_UNKNOWN','Reconcile outstanding mapping before replacement')
        if mapping is not None and mapping.state=='RECONCILIATION_REQUIRED':
            reject(409,'HAL_MAPPING_OUTCOME_UNKNOWN','Mapping outcome requires reconciliation')
        if mapping is None:
            mapping=HALV1Mapping(charger=c,scope_id=UUID(scope()))
        if mapping.state=='SYNCHRONIZED' and mapping.snapshot==snapshot:
            return {'charger_id':str(c.pk),'state':'SYNCHRONIZED','already_current':True}
        mapping.ocpp_identity=c.hal_identity
        mapping.snapshot=snapshot
        mapping.state='PENDING'
        mapping.failure_code=''
        mapping.save()
        _audit(actor,c.pk,'HAL_V1_MAPPING_PREPARED')
        return {'charger_id':str(c.pk),'state':'PENDING','already_current':False}


def send_mapping_once(actor,charger_id):
    _enabled('COW_HAL_V1_MAPPING_SYNC_ENABLED','HAL mapping sync')
    with transaction.atomic():
        actor=safety.fresh_admin(actor,'fleet.manage')
        safety.lock_gate()
        m=HALV1Mapping.objects.select_for_update().filter(charger_id=charger_id,scope_id=scope()).first()
        if m is None:
            reject(404,'HAL_MAPPING_UNKNOWN','No CoW HAL mapping record')
        if m.state!='PENDING':
            return m.state
        current=mapping_payload(inventory(charger_id,lock=True))
        safety.ensure_quiescent(charger_id=charger_id)
        if current!=m.snapshot:
            m.state='RECONCILIATION_REQUIRED';m.failure_code='INVENTORY_CHANGED'
            m.save(update_fields=['state','failure_code','updated_at'])
            return m.state
        m.state='ATTEMPTED';m.attempted_at=timezone.now()
        m.save(update_fields=['state','attempted_at','updated_at'])
        frozen=m.snapshot
    try:
        client().sync_mapping(str(charger_id),frozen,str(uuid4()))
        state,code='SYNCHRONIZED',''
    except Exception as exc:
        # Connection uncertainty is not evidence of no effect.
        state,code='RECONCILIATION_REQUIRED',('HAL_REMOTE_ERROR' if isinstance(exc,WireError) else 'HAL_LOCAL_FAILURE')
    with transaction.atomic():
        safety.lock_gate()
        m=HALV1Mapping.objects.select_for_update().get(charger_id=charger_id,scope_id=scope())
        if m.state=='ATTEMPTED' and m.snapshot==frozen:
            if state=='SYNCHRONIZED' and mapping_payload(inventory(charger_id,lock=True))!=frozen:
                state,code='RECONCILIATION_REQUIRED','INVENTORY_CHANGED_AFTER_HAL_IO'
            m.state=state;m.failure_code=code
            if state=='SYNCHRONIZED':m.synchronized_at=timezone.now()
            m.save()
        return m.state


def reconcile_mapping(actor, charger_id):
    """Explicitly re-submit ONLY the identical durable idempotent mapping.

    The Go HAL mapping PUT is keyed by cms_charger_id and immutable payload.
    It is not a physical OCPP command and cannot initiate charging. There is
    no automatic retry and drift/foreign-scope mappings are never replayed.
    """
    _enabled('COW_HAL_V1_MAPPING_SYNC_ENABLED','HAL mapping sync')
    with transaction.atomic():
        actor=safety.fresh_admin(actor,'fleet.manage')
        safety.lock_gate()
        charger=inventory(charger_id,lock=True)
        m=HALV1Mapping.objects.select_for_update().filter(charger=charger,scope_id=scope()).first()
        if m is None:
            reject(404,'HAL_MAPPING_UNKNOWN','No CoW mapping to reconcile')
        if m.state not in {'RECONCILIATION_REQUIRED','ATTEMPTED'}:
            reject(409,'HAL_MAPPING_NOT_UNCERTAIN','Only uncertain mappings can be reconciled')
        current=mapping_payload(charger)
        safety.ensure_quiescent(charger_id=charger.pk)
        if current!=m.snapshot or m.ocpp_identity!=charger.hal_identity:
            reject(409,'HAL_MAPPING_DRIFT','Mapping changed; do not replay')
        frozen=dict(m.snapshot)
        m.attempted_at=timezone.now()
        m.state='ATTEMPTED'
        m.save(update_fields=['attempted_at','state','updated_at'])
        _audit(actor,charger.pk,'HAL_V1_MAPPING_RECONCILIATION_REQUESTED')
    # Same Go idempotency key and identical frozen body. No arbitrary input.
    try:
        client().sync_mapping(str(charger_id),frozen,str(uuid4()))
        state,code='SYNCHRONIZED',''
    except Exception as exc:
        state,code='RECONCILIATION_REQUIRED',('HAL_REMOTE_ERROR' if isinstance(exc,WireError) else 'HAL_LOCAL_FAILURE')
    with transaction.atomic():
        safety.lock_gate()
        m=HALV1Mapping.objects.select_for_update().get(charger_id=charger_id,scope_id=scope())
        if m.state=='ATTEMPTED' and m.snapshot==frozen:
            if state=='SYNCHRONIZED' and mapping_payload(inventory(charger_id,lock=True))!=frozen:
                state,code='RECONCILIATION_REQUIRED','INVENTORY_CHANGED_AFTER_HAL_IO'
            m.state=state;m.failure_code=code
            if state=='SYNCHRONIZED':m.synchronized_at=timezone.now()
            m.save()
        return {'charger_id':str(charger_id),'state':m.state,'already_current':m.state=='SYNCHRONIZED'}


_OPS={'RESET','UNLOCK_CONNECTOR','CHANGE_AVAILABILITY','CLEAR_CACHE',
      'GET_CONFIGURATION','CHANGE_CONFIGURATION','TRIGGER_MESSAGE'}
_TRIGGERS={'BootNotification','DiagnosticsStatusNotification','FirmwareStatusNotification',
           'Heartbeat','MeterValues','StatusNotification'}
def parameters(kind, payload, connector):
    if kind not in _OPS or not isinstance(payload,dict):
        reject(422,'HAL_OPERATION_INVALID','Unsupported OCPP operation')
    if kind=='CHANGE_CONFIGURATION':
        reject(409, 'HAL_CONFIGURATION_CHANGE_DISABLED',
               'OCPP configuration writes require a separately reviewed per-model policy')
    keys=set(payload)
    if kind=='RESET' and connector is None and keys=={'type'} and payload['type'] in {'SOFT','HARD'}:
        return {'type':payload['type']},[]
    if kind=='UNLOCK_CONNECTOR' and connector is not None and not keys:
        return {},[]
    if kind=='CHANGE_AVAILABILITY' and keys=={'type'} and payload['type'] in {'OPERATIVE','INOPERATIVE'}:
        return {'type':payload['type']},[]
    if kind=='CLEAR_CACHE' and connector is None and not keys:
        return {},[]
    if kind=='GET_CONFIGURATION' and connector is None and keys=={'keys'}:
        values=payload['keys']
        if isinstance(values,list) and len(values)<=64 and all(isinstance(x,str) and 1<=len(x)<=100 and all(c.isalnum() and c.isascii() or c in '_-.' for c in x) for x in values):
            return {},values
    if kind=='TRIGGER_MESSAGE' and keys=={'requested_message'} and payload['requested_message'] in _TRIGGERS:
        return {'requested_message':payload['requested_message']},[]
    reject(422,'HAL_OPERATION_INVALID','Unsafe OCPP operation arguments')


def _digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def operation_view(op):
    return {'id':str(op.pk),'charger_id':str(op.charger_id),
            'connector_id':str(op.connector_id) if op.connector_id else None,
            'kind':op.kind,'state':op.state,
            'hal_operation_id':str(op.hal_operation_id) if op.hal_operation_id else None,
            'failure_code':op.failure_code,
            'provider_state':op.provider_state,'provider_ocpp_result':op.provider_ocpp_result,
            'result_summary':op.result_summary,
            'attempted_at':op.attempted_at.isoformat() if op.attempted_at else None,
            'resolved_at':op.resolved_at.isoformat() if op.resolved_at else None,
            'resolution_ref':op.resolution_ref}


def prepare_operation(actor,charger_id,connector_id,kind,payload,idempotency_key,reason):
    _enabled('COW_HAL_V1_ADMIN_OPERATIONS_ENABLED','HAL charger operations')
    if not isinstance(idempotency_key,str) or not 16<=len(idempotency_key)<=128 or any(ord(c)<33 or ord(c)>126 or c==',' for c in idempotency_key):
        reject(428,'HAL_IDEMPOTENCY_REQUIRED','Valid Idempotency-Key required')
    with transaction.atomic():
        actor=safety.fresh_admin(actor,'charging.command')
        safety.lock_gate()
        charger=inventory(charger_id,lock=True)
        connector=None
        if connector_id is not None:
            connector=ChargerConnector.objects.select_for_update().filter(pk=connector_id,charger=charger,active=True).first()
            if connector is None:
                reject(404,'HAL_CONNECTOR_NOT_FOUND','Unknown or disabled CoW connector')
        p,config_keys=parameters(kind,payload,connector)
        old=HALV1ChargerOperation.objects.select_for_update().filter(actor=actor,idempotency_key=idempotency_key).first()
        request={'charger_id':str(charger.pk),'connector_id':str(connector.pk) if connector else None,
                 'kind':kind,'parameters':p,'keys':config_keys,'reason':reason}
        digest=_digest(request)
        if old is not None:
            if old.request_digest!=digest:
                reject(409,'HAL_IDEMPOTENCY_CONFLICT','Operation idempotency key reused with different data')
            return operation_view(old)
        mapping=HALV1Mapping.objects.select_for_update().filter(charger=charger,scope_id=scope(),state='SYNCHRONIZED').first()
        if not mapping or mapping.snapshot!=mapping_payload(charger):
            reject(409,'HAL_MAPPING_NOT_READY','CoW charger mapping must be synchronized')
        # Prevent all disruptive operations while physical/operational resources are claimed.
        safety.ensure_quiescent(charger_id=charger.pk,include_pairing=False)
        op=HALV1ChargerOperation.objects.create(
            scope_id=UUID(scope()),charger=charger,connector=connector,actor=actor,
            idempotency_key=idempotency_key,request_digest=digest,correlation_id=uuid4(),
            trace_id=uuid4(),kind=kind,snapshot={
                'cms_operation_id':None, 'trace_id':None, 'cpo_id':scope(),
                'cms_charger_id':str(charger.pk),
                'cms_connector_id':str(connector.pk) if connector else '',
                'charger_ocpp_identity':charger.hal_identity,
                'ocpp_connector_number':connector.connector_number if connector else 0,
                'kind':kind,'parameters':p,'configuration_keys':config_keys,
            })
        op.snapshot['cms_operation_id']=str(op.pk)
        op.snapshot['trace_id']=str(op.trace_id)
        op.save(update_fields=['snapshot','updated_at'])
        _audit(actor,op.pk,'HAL_V1_OPERATION_CREATED',kind=kind,charger_id=str(charger.pk),reason=reason)
        return operation_view(op)


def send_operation_once(actor,operation_id):
    _enabled('COW_HAL_V1_ADMIN_OPERATIONS_ENABLED','HAL charger operations')
    with transaction.atomic():
        actor=safety.fresh_admin(actor,'charging.command')
        safety.lock_gate()
        op=HALV1ChargerOperation.objects.select_for_update().filter(pk=operation_id,scope_id=scope()).first()
        if op is None:
            reject(404,'HAL_OPERATION_NOT_FOUND','No CoW operation')
        if op.state!='PERSISTED':return op.state
        mapping=HALV1Mapping.objects.select_for_update().filter(charger_id=op.charger_id,scope_id=scope(),state='SYNCHRONIZED').first()
        if not mapping or mapping.snapshot!=mapping_payload(inventory(op.charger_id,lock=True)):
            # No remote dispatch happened; preserve local cancelability.
            reject(409,'HAL_MAPPING_CHANGED_BEFORE_SEND','Mapping drifted since staging; cancel unsent operation')
        snapshot=op.snapshot
        current_connector=ChargerConnector.objects.filter(pk=op.connector_id,charger_id=op.charger_id).first() if op.connector_id else None
        connector_id=str(current_connector.pk) if current_connector else ''
        connector_number=current_connector.connector_number if current_connector else 0
        if (snapshot.get('cms_operation_id')!=str(op.pk) or
                snapshot.get('trace_id')!=str(op.trace_id) or
                snapshot.get('cpo_id')!=str(op.scope_id) or
                snapshot.get('cms_charger_id')!=str(op.charger_id) or
                snapshot.get('charger_ocpp_identity')!=mapping.ocpp_identity or
                snapshot.get('cms_connector_id')!=connector_id or
                snapshot.get('ocpp_connector_number')!=connector_number or
                snapshot.get('kind')!=op.kind or
                (op.connector_id is not None and current_connector is None)):
            reject(409,'HAL_OPERATION_SNAPSHOT_STALE','Frozen operation no longer matches CoW mapping')
        safety.ensure_quiescent(charger_id=op.charger_id, exclude_hal_v1_operation_id=op.pk)
        op.state='DELIVERY_ATTEMPTED';op.attempted_at=timezone.now()
        op.save(update_fields=['state','attempted_at','updated_at'])
        frozen=op.snapshot.copy(); correlation=str(op.correlation_id)
    try:
        result=client().charger_operation(frozen,correlation)
        state,code,hal_id='RECEIPT_RECORDED','',UUID(result['operation']['hal_operation_id'])
    except Exception:
        state,code,hal_id='RECONCILIATION_REQUIRED','HAL_OUTCOME_UNKNOWN',None
        result={}
    with transaction.atomic():
        safety.lock_gate()
        op=HALV1ChargerOperation.objects.select_for_update().get(pk=operation_id,scope_id=scope())
        if op.state=='DELIVERY_ATTEMPTED':
            op.state=state;op.failure_code=code;op.hal_operation_id=hal_id
            op.provider_state=str(result.get('operation',{}).get('state',''))[:32]
            op.provider_ocpp_result=str(result.get('operation',{}).get('ocpp_result',''))[:80]
            op.result_summary=_safe_configuration(result.get('configuration'))
            op.outcome_at=timezone.now();op.save()
        return op.state


def cancel_unsent_operation(actor,operation_id,reason):
    """Cancel only a definitely local PERSISTED operation that never crossed IO."""
    if not isinstance(reason,str) or not 8<=len(reason.strip())<=500:
        reject(422,'HAL_RESOLUTION_REASON_INVALID','Cancellation reason required')
    with transaction.atomic():
        actor=safety.fresh_admin(actor,'charging.command')
        safety.lock_gate()
        op=HALV1ChargerOperation.objects.select_for_update().filter(pk=operation_id,scope_id=scope()).first()
        if op is None:reject(404,'HAL_OPERATION_NOT_FOUND','No CoW operation')
        if op.state=='CANCELLED_UNSENT':return operation_view(op)
        if op.state!='PERSISTED' or op.attempted_at is not None:
            reject(409,'HAL_OPERATION_ALREADY_ATTEMPTED','Only never-attempted operations can be cancelled')
        op.state='CANCELLED_UNSENT';op.resolved_at=timezone.now()
        op.save(update_fields=['state','resolved_at','updated_at'])
        _audit(actor,op.pk,'HAL_V1_OPERATION_CANCELLED_UNSENT',reason=reason)
        return operation_view(op)


def resolve_operation(actor,operation_id,reason,evidence_ref):
    """Close a technical operation ONLY on exact terminal OCPP acknowledgment.

    The human operator must separately inspect safe hardware/runtime evidence,
    reference it and explicitly close the safety hold. This is not the same as
    proving the physical effects of a Reset or software configuration change.
    """
    if (not isinstance(reason,str) or not 8<=len(reason.strip())<=500 or
        not isinstance(evidence_ref,str) or not 8<=len(evidence_ref.strip())<=256):
        reject(422,'HAL_RESOLUTION_EVIDENCE_REQUIRED','Reason and evidence reference required')
    with transaction.atomic():
        actor=safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        op=HALV1ChargerOperation.objects.select_for_update().filter(pk=operation_id,scope_id=scope()).first()
        if op is None:reject(404,'HAL_OPERATION_NOT_FOUND','No CoW operation')
        if op.state not in {'RECEIPT_RECORDED','RECONCILIATION_REQUIRED'} or op.attempted_at is None:
            reject(409,'HAL_OPERATION_NOT_READY','Only attempted operations can be resolved')
        recorded_id=op.hal_operation_id
        frozen=dict(op.snapshot)
    try:
        result=client().operation(str(operation_id),frozen)
        provider=result['operation']
        if provider['state']!='OCPP_CONFIRMED':
            reject(409,'HAL_OPERATION_NOT_CONFIRMED','Exact HAL OCPP acknowledgment is not confirmed')
        provider_id=UUID(provider['hal_operation_id'])
        if recorded_id is not None and recorded_id!=provider_id:
            reject(409,'HAL_OPERATION_IDENTITY_CONFLICT','HAL operation identity changed')
    except WireError:
        reject(503,'HAL_OPERATION_EVIDENCE_UNAVAILABLE','Exact HAL operation lookup unavailable')
    with transaction.atomic():
        actor=safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        op=HALV1ChargerOperation.objects.select_for_update().filter(pk=operation_id,scope_id=scope()).first()
        if op is None:reject(404,'HAL_OPERATION_NOT_FOUND','No CoW operation')
        if op.state not in {'RECEIPT_RECORDED','RECONCILIATION_REQUIRED'} or op.attempted_at is None:
            reject(409,'HAL_OPERATION_STATE_CHANGED','Operation changed while verifying evidence')
        if op.hal_operation_id not in (None,provider_id) or op.snapshot!=frozen:
            reject(409,'HAL_OPERATION_IDENTITY_CONFLICT','HAL operation identity changed')
        safety.ensure_quiescent(charger_id=op.charger_id,exclude_hal_v1_operation_id=op.pk)
        op.state='RESOLVED';op.resolved_at=timezone.now();op.resolution_ref=evidence_ref.strip()
        op.hal_operation_id=provider_id;op.provider_state='OCPP_CONFIRMED'
        op.provider_ocpp_result=str(provider.get('ocpp_result',''))[:80]
        op.failure_code=''
        op.save(update_fields=['state','resolved_at','resolution_ref','hal_operation_id',
                               'provider_state','provider_ocpp_result','failure_code','updated_at'])
        _audit(actor,op.pk,'HAL_V1_OPERATION_RESOLVED',reason=reason,
               evidence_ref=op.resolution_ref,provider_state=op.provider_state)
        return operation_view(op)


def read_operation(actor,operation_id):
    rbac.require(actor,'charging.read')
    op=HALV1ChargerOperation.objects.filter(pk=operation_id,scope_id=scope()).first()
    if op is None:reject(404,'HAL_OPERATION_NOT_FOUND','No CoW operation')
    return operation_view(op)


def reconcile_operation(actor,operation_id):
    """Single-attempt lookup, exact provider identity, monotone terminal state.

    Provider IO is outside the DB transaction. We re-authorize and re-read under
    the shared gate afterwards, because another operator may have resolved the
    original operation while our GET was in flight. No terminal state reopens.
    """
    terminal={'RESOLVED','CANCELLED_UNSENT','REJECTED'}
    with transaction.atomic():
        actor=safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        op=HALV1ChargerOperation.objects.select_for_update().filter(
            pk=operation_id,scope_id=scope()).first()
        if op is None:
            reject(404,'HAL_OPERATION_NOT_FOUND','No CoW operation')
        if op.state=='PERSISTED':
            reject(409,'HAL_NOT_SENT','Operation never dispatched')
        if op.state in terminal:
            return operation_view(op)
        if op.attempted_at is None:
            reject(409,'HAL_OPERATION_NOT_ATTEMPTED','Cannot reconcile an unsent operation')
        op_id=str(op.pk)
        frozen=dict(op.snapshot)

    try:
        result=client().operation(op_id,frozen)
        provider=result['operation']
        provider_id=UUID(provider['hal_operation_id'])
        lookup_ok=True
    except WireError as exc:
        # A scope/identity failure is a stronger conflict than a lost GET.
        lookup_ok=False
        identity_conflict=exc.code in {
            'HAL_IDENTITY_MISMATCH','HAL_OPERATION_CONNECTOR_MISMATCH',
            'HAL_OPERATION_KIND_MISMATCH', 'HAL_SCOPE_EXPECTATION_REQUIRED',
        }
        provider={}
        provider_id=None

    with transaction.atomic():
        actor=safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        op=HALV1ChargerOperation.objects.select_for_update().filter(
            pk=op_id,scope_id=scope()).first()
        if op is None:
            reject(404,'HAL_OPERATION_NOT_FOUND','No CoW operation')
        if op.state in terminal:
            # The real resolution/cancellation wins; no terminal regression.
            return operation_view(op)
        if op.state=='PERSISTED' or op.attempted_at is None:
            reject(409,'HAL_OPERATION_STATE_CHANGED','Operation changed during lookup')
        if op.snapshot != frozen:
            reject(409,'HAL_OPERATION_IDENTITY_CONFLICT','Operation scope changed during lookup')
        if not lookup_ok:
            # A lost GET does not erase an accepted receipt. Contradictory
            # provider identity *does* require an explicit safety hold.
            if identity_conflict:
                op.state='RECONCILIATION_REQUIRED'
                op.failure_code='HAL_PROVIDER_IDENTITY_INVALID'
                op.save(update_fields=['state','failure_code','updated_at'])
            elif op.state!='RECEIPT_RECORDED':
                op.state='RECONCILIATION_REQUIRED'
                op.failure_code='HAL_LOOKUP_INCONCLUSIVE'
                op.save(update_fields=['state','failure_code','updated_at'])
        elif op.hal_operation_id not in (None,provider_id):
            # Preserve the original durable identity; keep the safety hold.
            op.state='RECONCILIATION_REQUIRED'
            op.failure_code='HAL_OPERATION_IDENTITY_CONFLICT'
            op.save(update_fields=['state','failure_code','updated_at'])
        else:
            op.state='RECEIPT_RECORDED'
            op.hal_operation_id=provider_id
            op.failure_code=''
            op.provider_state=str(provider.get('state',''))[:32]
            op.provider_ocpp_result=str(provider.get('ocpp_result',''))[:80]
            op.save(update_fields=['state','hal_operation_id','failure_code',
                                   'provider_state','provider_ocpp_result','updated_at'])
        _audit(actor,op.pk,'HAL_V1_OPERATION_RECONCILED',state=op.state,
               failure_code=op.failure_code)
        return operation_view(op)


def charger_runtime(actor,charger_id):
    rbac.require(actor,'charging.read')
    c=inventory(charger_id)
    row=HALV1ChargerRuntime.objects.filter(charger=c).first()
    if row is None:return {'charger_id':str(c.pk),'connection_state':'UNKNOWN','fresh':False,'observed_at':None}
    fresh=(row.connection_state=='ONLINE' and row.observed_at is not None and
           timezone.now()-row.observed_at<=timedelta(seconds=settings.COW_HAL_V1_CONNECTION_STALE_SECONDS))
    return {'charger_id':str(c.pk),'connection_state':row.connection_state,
            'sequence':row.sequence,'fresh':bool(fresh),
            'observed_at':row.observed_at.isoformat() if row.observed_at else None}


def connector_runtime(actor,connector_id):
    rbac.require(actor,'charging.read')
    c=ChargerConnector.objects.select_related('charger').filter(pk=connector_id).first()
    if c is None:reject(404,'HAL_CONNECTOR_NOT_FOUND','CoW connector missing')
    parent=charger_runtime(actor,c.charger_id)
    row=HALV1ConnectorRuntime.objects.filter(connector=c).first()
    if row is None:return {'connector_id':str(c.pk),'status':'UNKNOWN','fresh':False}
    fresh=(parent['fresh'] and row.observed_at is not None and
           timezone.now()-row.observed_at<=timedelta(seconds=settings.COW_HAL_V1_CONNECTION_STALE_SECONDS))
    return {'connector_id':str(c.pk),'status':row.ocpp_status,'sequence':row.sequence,
            'fresh':bool(fresh),'observed_at':row.observed_at.isoformat() if row.observed_at else None}


def _safe_configuration(raw):
    """Only HAL-redacted config may persist; independently redact suspicious keys."""
    if not isinstance(raw,dict):return {}
    items=[]
    for entry in raw.get('configuration_keys',[])[:64]:
        if not isinstance(entry,dict):continue
        key=str(entry.get('key',''))[:100]
        sensitive=bool(entry.get('redacted')) or any(word in key.lower() for word in
            ('password','secret','token','privatekey','certificate','authorizationkey'))
        items.append({'key':key,'readonly':bool(entry.get('readonly')),
            'redacted':sensitive,'value':None if sensitive else str(entry.get('value',''))[:500]})
    return {'configuration_keys':items,
        'unknown_keys':[str(x)[:100] for x in raw.get('unknown_keys',[])[:64] if isinstance(x,str)]}
