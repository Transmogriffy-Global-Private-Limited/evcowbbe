"""Pure source-contract acceptance cases. NOT EXECUTED in source-only drop."""
import pytest
from cow.hal.v1_charge_contract import (
    ChargeContractError, canonical_uuid, valid_soc, validate_limits, ensure_id_chain,
)

A='123e4567-e89b-42d3-a456-426614174001'
B='123e4567-e89b-42d3-a456-426614174002'
C='123e4567-e89b-42d3-a456-426614174003'

def test_identifiers_are_canonical():
    assert canonical_uuid(A)==A
    for raw in (A.upper(), 'not-a-uuid', None, '00000000-0000-0000-0000-000000000000'):
        with pytest.raises(ChargeContractError): canonical_uuid(raw)

def test_soc_is_exact_and_bounded():
    assert valid_soc('63.500')=='63.500'
    for raw in (-1, '100.001', '100.0001', 'NaN', '1e1', '', None):
        with pytest.raises(ChargeContractError): valid_soc(raw)

def test_independent_limit_dimensions():
    validate_limits('AUTO',1000,'WALLET',600,'CUSTOMER_TIME')
    validate_limits('MONEY',1000,'CUSTOMER_MONEY',0,'NONE')
    for args in [('AUTO',0,'WALLET',0,'NONE'),('TIME',0,'NONE',1,'CUSTOMER_ENERGY')]:
        with pytest.raises(ChargeContractError):validate_limits(*args)

def test_identity_chain_never_infers_from_time_or_connector_alone():
    body=dict(cms_start_intent_id=A,cms_charger_id=B,cms_connector_id=C,
              charger_ocpp_identity='COW-001',ocpp_connector_number=1)
    ensure_id_chain(body,intent_id=A,charger_id=B,connector_id=C,identity='COW-001',connector_number=1)
    bad={**body,'cms_connector_id':A}
    with pytest.raises(ChargeContractError):
        ensure_id_chain(bad,intent_id=A,charger_id=B,connector_id=C,identity='COW-001',connector_number=1)
