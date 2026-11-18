"""Pure, side-effect-free validation for the unchanged Go HAL charging v1.

Source-only preflight. Source does not prove PostgreSQL, transport, or charger behavior.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID


class ChargeContractError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_uuid(raw) -> str:
    try:
        result = UUID(str(raw))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ChargeContractError('HAL_CHARGE_UUID_INVALID') from exc
    if result.int == 0 or str(result) != str(raw):
        raise ChargeContractError('HAL_CHARGE_UUID_NONCANONICAL')
    return str(result)


def strict_positive(value, label: str) -> int:
    if type(value) is not int or value <= 0 or value > 2**63 - 1:
        raise ChargeContractError('HAL_CHARGE_INVALID_' + label)
    return value


def nonnegative(value, label: str) -> int:
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise ChargeContractError('HAL_CHARGE_INVALID_' + label)
    return value


def utc_time(raw: str) -> datetime:
    if not isinstance(raw, str) or len(raw) > 40:
        raise ChargeContractError('HAL_CHARGE_TIME_INVALID')
    try:
        result = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ChargeContractError('HAL_CHARGE_TIME_INVALID') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ChargeContractError('HAL_CHARGE_TIME_INVALID')
    return result.astimezone(timezone.utc)


def valid_soc(raw: str) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 8:
        raise ChargeContractError('HAL_SOC_INVALID')
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ChargeContractError('HAL_SOC_INVALID') from exc
    if (not value.is_finite() or value < 0 or value > 100 or
        value.as_tuple().exponent < -3 or format(value, 'f') != raw):
        raise ChargeContractError('HAL_SOC_INVALID')
    return raw


START_STATES = frozenset({'PERSISTED', 'DELIVERY_ATTEMPTED', 'ACCEPTED_FOR_DELIVERY',
    'PROTOCOL_ACKNOWLEDGED', 'ACTUALLY_STARTED', 'REJECTED', 'EXPIRED', 'RECONCILIATION_REQUIRED'})
COMMAND_STATES = frozenset({'PERSISTED', 'DELIVERY_ATTEMPTED', 'ACK_RECORDED',
    'PROTOCOL_ACKNOWLEDGED', 'MATERIALIZED', 'REJECTED', 'EXPIRED', 'RECONCILIATION_REQUIRED'})
SESSION_STATES = frozenset({'ACTIVE', 'PHYSICALLY_COMPLETED', 'RECONCILIATION_REQUIRED', 'SETTLED'})
GO_COMMAND_STATES = frozenset({'PERSISTED', 'PENDING_DELIVERY', 'DELIVERY_ATTEMPTED',
    'OCPP_ACCEPTED', 'OCPP_REJECTED', 'AMBIGUOUS', 'EXPIRED', 'SUPERSEDED', 'MATERIALIZED'})
LIMIT_TYPES = frozenset({'AUTO', 'ENERGY', 'TIME', 'MONEY'})
ENERGY_SOURCES = frozenset({'NONE', 'CUSTOMER_ENERGY', 'CUSTOMER_MONEY', 'WALLET'})
DURATION_SOURCES = frozenset({'NONE', 'CUSTOMER_TIME', 'CUSTOMER_MONEY', 'WALLET'})


def validate_limits(limit_type: str, energy_wh: int, energy_source: str,
                    duration_seconds: int, duration_source: str) -> None:
    if limit_type not in LIMIT_TYPES:
        raise ChargeContractError('HAL_LIMIT_TYPE_INVALID')
    nonnegative(energy_wh, 'ENERGY_LIMIT')
    nonnegative(duration_seconds, 'DURATION_LIMIT')
    if (energy_source not in ENERGY_SOURCES or
            duration_source not in DURATION_SOURCES or
            (energy_wh == 0) != (energy_source == 'NONE') or
            (duration_seconds == 0) != (duration_source == 'NONE')):
        raise ChargeContractError('HAL_LIMIT_SOURCE_INVALID')


def ensure_id_chain(payload: dict, *, intent_id: str, charger_id: str,
                    connector_id: str, identity: str, connector_number: int,
                    hal_transaction_id: str | None = None, ocpp_transaction_id: int | None = None) -> None:
    if not isinstance(payload, dict):
        raise ChargeContractError('HAL_FACT_PAYLOAD_INVALID')
    for key, expected in (('cms_start_intent_id', intent_id),
                          ('cms_charger_id', charger_id),
                          ('cms_connector_id', connector_id)):
        if canonical_uuid(payload.get(key)) != canonical_uuid(expected):
            raise ChargeContractError('HAL_FACT_ID_CHAIN_MISMATCH')
    if (payload.get('charger_ocpp_identity') != identity or
            type(payload.get('ocpp_connector_number')) is not int or
            payload.get('ocpp_connector_number') != connector_number):
        raise ChargeContractError('HAL_FACT_CONNECTOR_MISMATCH')
    if hal_transaction_id is not None and canonical_uuid(payload.get('hal_transaction_id')) != canonical_uuid(hal_transaction_id):
        raise ChargeContractError('HAL_FACT_TRANSACTION_MISMATCH')
    if ocpp_transaction_id is not None and payload.get('ocpp_transaction_id') != ocpp_transaction_id:
        raise ChargeContractError('HAL_FACT_TRANSACTION_MISMATCH')
