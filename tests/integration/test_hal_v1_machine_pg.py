"""Go HAL v1 machine PostgreSQL tests. Scratch database ONLY, never Aiven.

The guarded harness must set COW_PG_TEST_HARNESS_READY=1.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid4
import hashlib
import json

import pytest
from django.test import override_settings
from django.utils import timezone as tz

from cow.common.errors import DomainError
from cow.hal import v1_machine as machine, v1_ingress
from cow.models import (Account, AdminRole, Principal, Charger, ChargerConnector,
                        HALV1Mapping, HALV1ChargerRuntime, HALV1FactReceipt,
                        HALV1ChargerOperation)

pytestmark=[pytest.mark.postgres,pytest.mark.django_db(transaction=True)]
SCOPE='f507864d-f5b9-4c6c-9503-0e4b6e72a441'
TOKEN='go-hal-fact-synthetic-test-'+'t'*40


class Request:
    content_type='application/json'
    def __init__(self,payload):
        self.body=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
        self.headers={'Authorization':'Bearer '+TOKEN,'Idempotency-Key':payload['fact_id']}


def setup():
    account=Account.objects.create(email=f'hal-{uuid4().hex}@example.invalid',password_hash='none',email_verified=True)
    admin=Principal.objects.create(account=account,plane='admin',status='ACTIVE')
    AdminRole.objects.create(principal=admin,role='FULL_ADMIN')
    charger=Charger.objects.create(asset_tag='halv1-'+uuid4().hex[:15],
        hal_identity='COW-'+uuid4().hex[:15],manufacturer='Test',model='30kW')
    connector=ChargerConnector.objects.create(charger=charger,connector_number=1,
        connector_type='CCS2',max_power_w=30000,supports_dc=True,supports_ac=False)
    return admin,charger,connector


def prepared(charger):
    from cow.hal.v1_machine import mapping_payload
    return HALV1Mapping.objects.create(charger=charger,scope_id=UUID(SCOPE),
        ocpp_identity=charger.hal_identity,snapshot=mapping_payload(charger),
        state='SYNCHRONIZED')


def fact(charger,seq=1,kind='charger.connection.updated'):
    import rfc8785
    p={'cpo_id':SCOPE,'cms_charger_id':str(charger.pk),
       'charger_ocpp_identity':charger.hal_identity,'connection_state':'ONLINE',
       'connection_generation':1,'connection_sequence':seq,
       'observed_at':'2026-11-13T10:00:00Z'}
    e={'fact_id':str(uuid4()),'fact_type':kind,'schema_version':1,
       'occurred_at':'2026-11-13T10:00:00Z','producer':'ocpp-hal-go-new','payload':p}
    e['immutable_content_sha256']=hashlib.sha256(rfc8785.dumps(e)).hexdigest()
    return e


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_MAPPING_SYNC_ENABLED=True)
def test_mapping_persist_and_idempotent_prepare():
    actor,c,connector=setup()
    first=machine.prepare_mapping(actor,c.pk)
    assert first['state']=='PENDING'
    m=HALV1Mapping.objects.get(charger=c)
    assert m.snapshot['cpo_id']==SCOPE and m.snapshot['connectors'][0]['cms_connector_id']==str(connector.pk)
    m.state='SYNCHRONIZED';m.save()
    second=machine.prepare_mapping(actor,c.pk)
    assert second['already_current'] is True


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_FACT_BEARER_TOKEN=TOKEN,
                   COW_HAL_V1_CONNECTION_STALE_SECONDS=900)
def test_fact_jcs_dedupe_ordering_foreign_scope_and_integrity():
    actor,c,connector=setup()
    prepared(c)
    e=fact(c,seq=2)
    v1_ingress.ingest_fact(Request(e))
    v1_ingress.ingest_fact(Request(e))
    assert HALV1FactReceipt.objects.filter(fact_id=e['fact_id']).count()==1
    assert HALV1ChargerRuntime.objects.get(charger=c).sequence==2
    older=fact(c,seq=1)
    v1_ingress.ingest_fact(Request(older))
    assert HALV1ChargerRuntime.objects.get(charger=c).sequence==2
    tamper=dict(e);tamper['immutable_content_sha256']='0'*64
    with pytest.raises(DomainError) as bad:v1_ingress.ingest_fact(Request(tamper))
    assert bad.value.status_code==409
    foreign=fact(c,seq=3)
    foreign['payload']['cpo_id']=str(uuid4())
    import rfc8785
    foreign['immutable_content_sha256']=hashlib.sha256(rfc8785.dumps({k:v for k,v in foreign.items() if k!='immutable_content_sha256'})).hexdigest()
    with pytest.raises(DomainError) as wrong:v1_ingress.ingest_fact(Request(foreign))
    assert wrong.value.status_code==403
    assert HALV1FactReceipt.objects.count()==2


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_ADMIN_OPERATIONS_ENABLED=True)
def test_admin_operation_staged_never_sends_without_explicit_dispatch(monkeypatch):
    actor,c,connector=setup();prepared(c)
    idem='hal-test-idempotency-'+uuid4().hex
    first=machine.prepare_operation(actor,c.pk,None,'GET_CONFIGURATION',{'keys':['HeartbeatInterval']},idem,
                                    'Technical diagnostics requested')
    assert first['state']=='PERSISTED'
    again=machine.prepare_operation(actor,c.pk,None,'GET_CONFIGURATION',{'keys':['HeartbeatInterval']},idem,
                                    'Technical diagnostics requested')
    assert again['id']==first['id']
    with pytest.raises(DomainError):
        machine.prepare_operation(actor,c.pk,None,'GET_CONFIGURATION',{'keys':['MeterValuesSampledData']},idem,
                                  'Technical diagnostics requested')
    calls=[]
    class Down:
        def charger_operation(self,payload,correlation):
            calls.append(payload)
            raise ConnectionError('uncertain')
    monkeypatch.setattr(machine,'client',lambda:Down())
    assert machine.send_operation_once(actor,first['id'])=='RECONCILIATION_REQUIRED'
    assert machine.send_operation_once(actor,first['id'])=='RECONCILIATION_REQUIRED'
    assert len(calls)==1
    assert HALV1ChargerOperation.objects.get(pk=first['id']).attempted_at is not None


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_ADMIN_OPERATIONS_ENABLED=True)
def test_unsent_cancel_releases_machine_operation_claim():
    actor,c,connector=setup();prepared(c)
    first=machine.prepare_operation(actor,c.pk,None,'GET_CONFIGURATION',{'keys':[]},
         'v1-cancel-'+uuid4().hex,'Operator cancelled diagnostic read')
    from django.db import transaction
    from cow.services import operational_eligibility as safety
    with transaction.atomic():
        safety.lock_gate()
        with pytest.raises(DomainError) as blocked:
            safety.ensure_quiescent(charger_id=c.pk)
        assert blocked.value.code=='ASSET_OPERATION_BLOCKED'
    result=machine.cancel_unsent_operation(actor,first['id'],'Unsent diagnostic no longer needed')
    assert result['state']=='CANCELLED_UNSENT' and result['resolved_at']
    assert machine.cancel_unsent_operation(actor,first['id'],'Repeated cancellation')['state']=='CANCELLED_UNSENT'
    from django.db import transaction
    from cow.services import operational_eligibility as safety
    with transaction.atomic():
        safety.lock_gate()
        safety.ensure_quiescent(charger_id=c.pk)


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_ADMIN_OPERATIONS_ENABLED=True)
def test_confirmed_operation_requires_explicit_audited_resolution(monkeypatch):
    from django.db import transaction
    from cow.services import operational_eligibility as safety
    actor, charger, connector = setup()
    prepared(charger)
    item = machine.prepare_operation(
        actor, charger.pk, None, 'GET_CONFIGURATION', {'keys': []},
        'confirm-'+uuid4().hex, 'Inspect charger configuration',
    )
    hal_operation_id = str(uuid4())
    class Confirmed:
        def charger_operation(self, payload, correlation):
            return {'operation': {'hal_operation_id': hal_operation_id,
                    'cms_operation_id': item['id'], 'kind': 'GET_CONFIGURATION',
                    'state': 'OCPP_CONFIRMED', 'ocpp_result': 'Accepted',
                    'updated_at': '2026-11-13T10:00:00Z'}}
        def operation(self, op_id, expected):
            return self.charger_operation({}, '')
    monkeypatch.setattr(machine, 'client', lambda: Confirmed())
    assert machine.send_operation_once(actor, item['id']) == 'RECEIPT_RECORDED'
    with transaction.atomic():
        safety.lock_gate()
        with pytest.raises(DomainError):
            safety.ensure_quiescent(charger_id=charger.pk)
    result = machine.resolve_operation(
        actor, item['id'], 'Technician verified charger is safely idle',
        'FIELD-INSPECTION-20261113',
    )
    assert result['state'] == 'RESOLVED'
    assert result['provider_state'] == 'OCPP_CONFIRMED'
    assert result['resolution_ref'] == 'FIELD-INSPECTION-20261113'
    with transaction.atomic():
        safety.lock_gate()
        safety.ensure_quiescent(charger_id=charger.pk)


# HAL v1 M1 hardening: negative, lifecycle, and database evidence tests.
@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_FACT_BEARER_TOKEN=TOKEN)
def test_fact_rejects_partial_identity_and_stale_inventory():
    import rfc8785
    from cow.models import HALV1FactReceipt
    actor, charger, connector = setup()
    prepared(charger)
    def resign(event):
        event['immutable_content_sha256'] = hashlib.sha256(rfc8785.dumps(
            {k:v for k,v in event.items() if k!='immutable_content_sha256'})).hexdigest()
        return event
    for field in ('cms_charger_id','charger_ocpp_identity'):
        event=fact(charger)
        event['payload'].pop(field)
        with pytest.raises(DomainError):
            v1_ingress.ingest_fact(Request(resign(event)))
    assert HALV1FactReceipt.objects.count()==0
    changed=fact(charger)
    charger.hal_identity='COW-CHANGED-'+uuid4().hex[:8]
    charger.save(update_fields=['hal_identity','updated_at'])
    with pytest.raises(DomainError) as drift:
        v1_ingress.ingest_fact(Request(changed))
    assert drift.value.code=='HAL_V1_MAPPING_DRIFT'
    assert HALV1FactReceipt.objects.count()==0
    assert not HALV1ChargerRuntime.objects.filter(charger=charger).exists()


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_MAPPING_SYNC_ENABLED=False)
def test_disabled_mapping_reconcile_never_reaches_provider(monkeypatch):
    actor,charger,connector=setup()
    row=prepared(charger)
    row.state='RECONCILIATION_REQUIRED'
    row.save(update_fields=['state','updated_at'])
    def forbidden():
        raise AssertionError('provider must never be contacted with sync switch off')
    monkeypatch.setattr(machine,'client',forbidden)
    with pytest.raises(DomainError) as disabled:
        machine.reconcile_mapping(actor,charger.pk)
    assert disabled.value.code=='HAL_V1_DISABLED'
    row.refresh_from_db()
    assert row.state=='RECONCILIATION_REQUIRED'


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE)
def test_cancelled_and_resolved_operations_never_reopen(monkeypatch):
    actor,charger,connector=setup()
    prepared(charger)
    idem='terminal-'+uuid4().hex
    from django.test import override_settings as local_override
    with local_override(COW_HAL_V1_ADMIN_OPERATIONS_ENABLED=True):
        item=machine.prepare_operation(actor,charger.pk,None,'GET_CONFIGURATION',{'keys':[]},
            idem,'Inspect charger without an active session')
        machine.cancel_unsent_operation(actor,item['id'],'Cancelled before network IO')
    def forbidden():
        raise AssertionError('terminal operations must not call Go HAL')
    monkeypatch.setattr(machine,'client',forbidden)
    assert machine.reconcile_operation(actor,item['id'])['state']=='CANCELLED_UNSENT'
    op=HALV1ChargerOperation.objects.get(pk=item['id'])
    op.state='RESOLVED'
    op.attempted_at=tz.now()
    op.resolved_at=tz.now()
    op.save(update_fields=['state','attempted_at','resolved_at','updated_at'])
    assert machine.reconcile_operation(actor,item['id'])['state']=='RESOLVED'
    op.refresh_from_db()
    assert op.state=='RESOLVED'


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_ADMIN_OPERATIONS_ENABLED=True)
def test_reconcile_conflicting_hal_operation_id_preserves_original(monkeypatch):
    actor,charger,connector=setup()
    prepared(charger)
    item=machine.prepare_operation(actor,charger.pk,None,'GET_CONFIGURATION',{'keys':[]},
        'conflict-'+uuid4().hex,'Evidence of conflicting HAL identity')
    op=HALV1ChargerOperation.objects.get(pk=item['id'])
    existing=uuid4()
    op.hal_operation_id=existing
    op.state='RECEIPT_RECORDED'
    op.attempted_at=tz.now()
    op.save(update_fields=['hal_operation_id','state','attempted_at','updated_at'])
    class Provider:
        def operation(self,ident,expected):
            assert ident==str(op.pk) and expected==op.snapshot
            return {'operation':{'hal_operation_id':str(uuid4()),'state':'OCPP_CONFIRMED',
                'ocpp_result':'Accepted'}}
    monkeypatch.setattr(machine,'client',lambda:Provider())
    result=machine.reconcile_operation(actor,item['id'])
    assert result['state']=='RECONCILIATION_REQUIRED'
    assert result['failure_code']=='HAL_OPERATION_IDENTITY_CONFLICT'
    assert HALV1ChargerOperation.objects.get(pk=op.pk).hal_operation_id==existing


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_ADMIN_OPERATIONS_ENABLED=True)
def test_reconcile_race_does_not_reopen_terminal_resolution(monkeypatch):
    actor,charger,connector=setup()
    prepared(charger)
    item=machine.prepare_operation(actor,charger.pk,None,'GET_CONFIGURATION',{'keys':[]},
        'race-'+uuid4().hex,'Inspect previous OCPP result')
    op=HALV1ChargerOperation.objects.get(pk=item['id'])
    op.state='RECONCILIATION_REQUIRED'
    op.attempted_at=tz.now()
    op.save(update_fields=['state','attempted_at','updated_at'])
    class RacingProvider:
        def operation(self,ident,expected):
            HALV1ChargerOperation.objects.filter(pk=ident).update(
                state='RESOLVED',resolved_at=tz.now(),resolution_ref='FIELD-EVIDENCE-TEST')
            return {'operation':{'hal_operation_id':str(uuid4()),
                    'state':'OCPP_CONFIRMED','ocpp_result':'Accepted'}}
    monkeypatch.setattr(machine,'client',lambda:RacingProvider())
    assert machine.reconcile_operation(actor,item['id'])['state']=='RESOLVED'
    op.refresh_from_db()
    assert op.state=='RESOLVED' and op.resolution_ref=='FIELD-EVIDENCE-TEST'


def test_configuration_writes_default_denied():
    with pytest.raises(DomainError) as disabled:
        machine.parameters('CHANGE_CONFIGURATION',{'key':'VendorFeatureFlag','value':'true'},None)
    assert disabled.value.code=='HAL_CONFIGURATION_CHANGE_DISABLED'


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_FACT_BEARER_TOKEN=TOKEN)
def test_fact_evidence_immutable_in_postgresql_but_processing_state_mutable():
    from django.db import DatabaseError, transaction
    from cow.models import HALV1FactReceipt, HALV1TraceReceipt
    actor,charger,connector=setup()
    prepared(charger)
    event=fact(charger)
    v1_ingress.ingest_fact(Request(event))
    assert HALV1FactReceipt.objects.filter(fact_id=event['fact_id']).update(
        processing_state='PROCESSED')==1
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            HALV1FactReceipt.objects.filter(fact_id=event['fact_id']).update(digest='0'*64)
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            HALV1FactReceipt.objects.filter(fact_id=event['fact_id']).delete()
    assert HALV1FactReceipt.objects.get(fact_id=event['fact_id']).processing_state=='PROCESSED'
    trace=HALV1TraceReceipt.objects.create(event_id=uuid4(),scope_id=UUID(SCOPE),
        charger=charger,digest='1'*64,event_type='DIAGNOSTIC',payload={'summary':'original'})
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            HALV1TraceReceipt.objects.filter(pk=trace.pk).update(payload={'summary':'tampered'})
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            HALV1TraceReceipt.objects.filter(pk=trace.pk).delete()
    assert HALV1TraceReceipt.objects.get(pk=trace.pk).payload['summary']=='original'


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_MAPPING_SYNC_ENABLED=True)
def test_mapping_inventory_change_during_hal_io_never_reports_synchronized(monkeypatch):
    actor,charger,connector=setup()
    machine.prepare_mapping(actor,charger.pk)
    class Provider:
        def sync_mapping(self,charger_id,payload,correlation):
            assert charger_id==str(charger.pk)
            charger.hal_identity='COW-ROTATED-'+uuid4().hex[:10]
            charger.save(update_fields=['hal_identity','updated_at'])
            return {'mapping':payload}
    monkeypatch.setattr(machine,'client',lambda:Provider())
    assert machine.send_mapping_once(actor,charger.pk)=='RECONCILIATION_REQUIRED'
    mapping=HALV1Mapping.objects.get(charger=charger)
    assert mapping.failure_code=='INVENTORY_CHANGED_AFTER_HAL_IO'
    assert mapping.synchronized_at is None


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE,COW_HAL_V1_ADMIN_OPERATIONS_ENABLED=True)
def test_prepared_operation_never_sends_on_changed_mapping(monkeypatch):
    actor,charger,connector=setup()
    mapping=prepared(charger)
    op=machine.prepare_operation(actor,charger.pk,None,'GET_CONFIGURATION',{'keys':[]},
        'frozen-'+uuid4().hex,'Read charger diagnostics')
    charger.hal_identity='COW-NEW-'+uuid4().hex[:10]
    charger.save(update_fields=['hal_identity','updated_at'])
    mapping.ocpp_identity=charger.hal_identity
    mapping.snapshot=machine.mapping_payload(charger)
    mapping.save(update_fields=['ocpp_identity','snapshot','updated_at'])
    def forbidden():raise AssertionError('stale operation must never reach HAL')
    monkeypatch.setattr(machine,'client',forbidden)
    with pytest.raises(DomainError) as stale:
        machine.send_operation_once(actor,op['id'])
    assert stale.value.code=='HAL_OPERATION_SNAPSHOT_STALE'
    record=HALV1ChargerOperation.objects.get(pk=op['id'])
    assert record.state=='PERSISTED' and record.attempted_at is None
    machine.cancel_unsent_operation(actor,op['id'],'No longer safe under mapping changes')
