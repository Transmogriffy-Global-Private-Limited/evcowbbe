"""Adversarial Go-HAL response contract tests, no Django or network."""
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest

from cow.hal.v1_wire import V1Client, WireError

SCOPE='123e4567-e89b-42d3-a456-426614174020'
CHARGER='123e4567-e89b-42d3-a456-426614174021'
CONNECTOR='123e4567-e89b-42d3-a456-426614174022'
OP='123e4567-e89b-42d3-a456-426614174023'
HAL='123e4567-e89b-42d3-a456-426614174024'
CORRELATION='123e4567-e89b-42d3-a456-426614174025'
OTHER='123e4567-e89b-42d3-a456-426614174026'
TOKEN='integration-test-token-not-for-use-'+'t'*40


def make(result, status=202):
    return V1Client('http://127.0.0.1:18080',TOKEN,transport=httpx.MockTransport(
        lambda r:httpx.Response(status,json=result)))


def operation(connector=False):
    payload={'cms_operation_id':OP,'cpo_id':SCOPE,'cms_charger_id':CHARGER,
             'cms_connector_id':CONNECTOR if connector else '',
             'ocpp_connector_number':1 if connector else 0,'kind':'RESET'}
    receipt={**payload,'hal_operation_id':HAL,'state':'OCPP_CONFIRMED',
             'updated_at':'2026-11-16T08:00:00Z'}
    if not connector:receipt.pop('cms_connector_id')
    return payload,{'operation':receipt}


@pytest.mark.parametrize('field,replacement',[
    ('cms_operation_id',OTHER),('cpo_id',OTHER),('cms_charger_id',OTHER),
    ('cms_connector_id',OTHER),('ocpp_connector_number',2),
    ('kind','CHANGE_AVAILABILITY'),('hal_operation_id','invalid'),
])
def test_post_receipt_cannot_escape_frozen_scope(field,replacement):
    payload,response=operation(True)
    response['operation'][field]=replacement
    with pytest.raises(WireError):make(response).charger_operation(payload,CORRELATION)


def test_get_receipt_must_have_frozen_scope():
    payload,response=operation()
    with pytest.raises(WireError,match='HAL_SCOPE_EXPECTATION_REQUIRED'):
        make(response,200).operation(OP,{'cms_operation_id':OP})
    with pytest.raises(WireError,match='HAL_SCOPE_EXPECTATION_REQUIRED'):
        make(response,200).operation(OP,{'cms_operation_id':OTHER})
    assert make(response,200).operation(OP,payload)['operation']['hal_operation_id']==HAL


def test_scope_response_requires_all_identity_fields():
    payload,response=operation()
    for field in ('cpo_id','cms_charger_id','ocpp_connector_number','kind'):
        bad=deepcopy(response); bad['operation'].pop(field)
        with pytest.raises(WireError):make(bad).charger_operation(payload,CORRELATION)


def mapping():
    payload={'cpo_id':SCOPE,'cms_charger_id':CHARGER,
             'charger_ocpp_identity':'COW-STRICT-1','expected_serial':'SER-12',
             'enabled':True,'connectors':[{'cms_connector_id':CONNECTOR,
                 'ocpp_connector_number':1}]}
    echo=deepcopy(payload)
    echo['connectors'][0].update(cpo_id=SCOPE,cms_charger_id=CHARGER,
                                 charger_ocpp_identity='COW-STRICT-1')
    return payload,{'mapping':echo}


@pytest.mark.parametrize('key,replacement',[
    ('cpo_id',OTHER),('cms_charger_id',OTHER),
    ('charger_ocpp_identity','OTHER-OCPP'),('expected_serial','OTHER-SERIAL'),
    ('enabled',False),('connectors',[{'cms_connector_id':OTHER,'ocpp_connector_number':1}]),
])
def test_mapping_echo_must_be_exact(key,replacement):
    payload,receipt=mapping()
    receipt['mapping'][key]=replacement
    with pytest.raises(WireError):make(receipt,201).sync_mapping(CHARGER,payload,CORRELATION)


def test_mapping_echo_allows_order_independent_connector_map():
    payload,receipt=mapping()
    extra='123e4567-e89b-42d3-a456-426614174027'
    payload['connectors'].append({'cms_connector_id':extra,'ocpp_connector_number':2})
    receipt['mapping']['connectors'].append({'cms_connector_id':extra,
        'ocpp_connector_number':2,'cpo_id':SCOPE,'cms_charger_id':CHARGER,
        'charger_ocpp_identity':'COW-STRICT-1'})
    receipt['mapping']['connectors'].reverse()
    assert make(receipt,201).sync_mapping(CHARGER,payload,CORRELATION)['mapping']['enabled'] is True


def test_operation_id_and_echo_validation_does_not_follow_redirects():
    payload,_=operation()
    calls=[]
    def redirect(request):
        calls.append(request)
        return httpx.Response(307,headers={'Location':'https://other.example/v1/charger-operations'})
    c=V1Client('http://127.0.0.1:18080',TOKEN,transport=httpx.MockTransport(redirect))
    with pytest.raises(WireError):c.charger_operation(payload,CORRELATION)
    assert len(calls)==1
