"""Offline HAL v1 client contract tests; no Django, DB, Git, or network."""
from uuid import UUID
import httpx
import pytest

from cow.hal.v1_wire import V1Client, WireError, uid, endpoint

A='123e4567-e89b-42d3-a456-426614174000'
B='123e4567-e89b-42d3-a456-426614174001'
C='123e4567-e89b-42d3-a456-426614174002'
D='123e4567-e89b-42d3-a456-426614174003'
TOKEN='test-token-not-for-production-'+'t'*40


def make(handler):
    return V1Client('http://127.0.0.1:18080',TOKEN,transport=httpx.MockTransport(handler))


def test_url_and_uuid_boundary():
    assert endpoint('https://cow-hal.example')=='https://cow-hal.example'
    for bad in ('http://remote.example','https://user:pw@x','https://x/path',
                'https://x?a=1','file:///tmp/x','http://0.0.0.0:80','https://x#evil'):
        with pytest.raises(WireError):endpoint(bad)
    with pytest.raises(WireError):uid(UUID(int=0))
    with pytest.raises(WireError):uid(A.upper())
    assert uid(A)==A


def test_mapping_headers_exact_id_scope():
    seen=[]
    def handle(r):
        seen.append(r)
        return httpx.Response(201,json={'mapping':{
            'cpo_id':B,'cms_charger_id':A,'charger_ocpp_identity':'COW-001',
            'enabled':True,'connectors':[{'cms_connector_id':D,
                'ocpp_connector_number':1,'cpo_id':B,'cms_charger_id':A,
                'charger_ocpp_identity':'COW-001'}]}})
    c=make(handle)
    mapping={'cpo_id':B,'cms_charger_id':A,'charger_ocpp_identity':'COW-001',
             'enabled':True,'connectors':[{'cms_connector_id':D,
                 'ocpp_connector_number':1}]}
    assert c.sync_mapping(A,mapping,C)['mapping']['cms_charger_id']==A
    r=seen[0]
    assert r.method=='PUT' and r.url.path=='/v1/mappings/chargers/'+A
    assert r.headers['Idempotency-Key']==A
    assert r.headers['X-Correlation-ID']==C
    assert r.headers['Authorization']=='Bearer '+TOKEN
    assert r.content


def test_start_ack_only_command_not_physical_start():
    seen=[]
    def handle(r):
        seen.append(r)
        return httpx.Response(202,json={'command':{'cms_command_id':A,'hal_command_id':B,
            'kind':'START','state':'ACCEPTED_FOR_DELIVERY','updated_at':'2026-11-13T12:00:00Z'}})
    r=make(handle).start({'cms_command_id':A},C)
    assert r['command']['kind']=='START'
    assert seen[0].url.path=='/v1/remote-commands/start'
    assert seen[0].headers['Idempotency-Key']==A
    assert seen[0].headers['X-Correlation-ID']==C


def test_wrong_command_identity_is_not_success():
    def handle(r):
        return httpx.Response(202,json={'command':{'cms_command_id':D,'hal_command_id':B,
                'kind':'START','state':'PERSISTED','updated_at':'2026-11-13T12:00:00Z'}})
    with pytest.raises(WireError,match='HAL_IDENTITY_MISMATCH'):
        make(handle).start({'cms_command_id':A},C)


def test_redirect_does_not_follow_or_replay():
    calls=[]
    def handle(r):
        calls.append(r)
        return httpx.Response(307,headers={'Location':'https://wrong-host.example/v1/remote-commands/start'})
    with pytest.raises(WireError) as err:
        make(handle).start({'cms_command_id':A},C)
    assert err.value.status==307 and len(calls)==1


def test_timeout_ambiguous_no_automatic_replay():
    count=[0]
    def handle(r):
        count[0]+=1
        raise httpx.ReadTimeout('lost answer')
    with pytest.raises(WireError,match='HAL_OUTCOME_UNKNOWN'):
        make(handle).charger_operation({'cms_operation_id':A,'kind':'RESET'},C)
    assert count[0]==1


def test_command_exact_lookup():
    def handle(r):
        assert r.method=='GET' and r.url.query==('cms_command_id='+A).encode()
        return httpx.Response(200,json={'command':{'cms_command_id':A,'hal_command_id':B,
            'kind':'STOP','state':'AMBIGUOUS','updated_at':'2026-11-13T12:00:00Z'}})
    assert make(handle).command(A)['command']['state']=='AMBIGUOUS'


def test_transaction_exact_identity():
    def handle(r):
        return httpx.Response(200,json={'transaction':{'hal_transaction_id':A,
            'cms_start_intent_id':B,'cms_charger_id':C,'cms_connector_id':D,
            'ocpp_transaction_id':23}})
    c=make(handle)
    assert c.transaction(A)['transaction']['ocpp_transaction_id']==23
    assert c.transaction_for_intent(B)['transaction']['cms_start_intent_id']==B


def test_admin_operation_contract():
    def handle(r):
        assert r.url.path=='/v1/charger-operations'
        return httpx.Response(202,json={'operation':{'cms_operation_id':A,
            'hal_operation_id':B,'cpo_id':C,'cms_charger_id':D,
            'ocpp_connector_number':0,'kind':'RESET','state':'PERSISTED',
            'updated_at':'2026-11-13T12:00:00Z'}})
    result=make(handle).charger_operation({'cms_operation_id':A,'cpo_id':C,
        'cms_charger_id':D,'cms_connector_id':'','ocpp_connector_number':0,
        'kind':'RESET'},C)
    assert result['operation']['hal_operation_id']==B


def test_requeue_fact_exact_identity():
    def handle(r):
        assert r.url.path=='/v1/facts/'+A+'/requeue'
        return httpx.Response(202,json={'fact_id':A,'status':'PENDING'})
    assert make(handle).requeue_fact(A,C)['status']=='PENDING'


def test_foreign_response_and_invalid_config_are_rejected():
    def handler(r):return httpx.Response(200,json={'operation':{'hal_operation_id':A,
        'cms_operation_id':B,'kind':'RESET','state':'PERSISTED','updated_at':'2026-11-13T00:00:00Z'}})
    with pytest.raises(WireError):make(handler).operation(A,{
        'cms_operation_id':A,'cpo_id':C,'cms_charger_id':D,
        'cms_connector_id':'','ocpp_connector_number':0,'kind':'RESET'})
    with pytest.raises(WireError):make(handler).configuration(A,B,'COW-01',['a/b'])
