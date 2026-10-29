"""Independent of Go/OCPP. Canonical wire contract cow-hal.v1.

The Go HAL MUST pass tests/contract/test_hal_conformance.py before live attachment.
"""
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

VERSION = "cow-hal.v1"
COMMANDS = frozenset({"StartCharging", "StopCharging", "Reset", "UnlockConnector", "SetChargingLimit", "ChangeAvailability", "GetCapabilities"})
FACTS = frozenset({"ChargerConnected", "ChargerDisconnected", "ConnectorStateChanged", "TransactionStarted", "TransactionEnded", "MeterSampleObserved", "FaultObserved", "CommandAccepted", "CommandRejected", "CommandOutcomeUnknown", "CapabilityChanged"})


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sign(secret: str, epoch: int, body: bytes) -> str:
    return hmac.new(secret.encode(), str(epoch).encode()+b"."+body, hashlib.sha256).hexdigest()


def verify(secret: str, epoch: int, body: bytes, signature: str, now_epoch: int, skew_s=120):
    if not secret or len(secret) < 32 or abs(now_epoch-epoch) > skew_s:
        return False
    return hmac.compare_digest(sign(secret,epoch,body), signature)


class FactContractError(ValueError):
    pass


def validate_fact(fact: dict):
    if not isinstance(fact, dict) or set(fact) != {"contract", "event_id", "kind", "charger_identity", "emitted_at", "data"}:
        raise FactContractError("HAL fact keys mismatch contract")
    if fact["contract"] != VERSION or fact["kind"] not in FACTS:
        raise FactContractError("HAL contract version/kind unsupported")
    try:
        UUID(fact["event_id"])
        dt = datetime.fromisoformat(fact["emitted_at"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("naive timestamp")
    except (ValueError, TypeError, AttributeError) as exc:
        raise FactContractError("Invalid event_id/time") from exc
    if not isinstance(fact["charger_identity"], str) or not fact["charger_identity"] or not isinstance(fact["data"], dict):
        raise FactContractError("Invalid charger identity or data")
    data = fact["data"]
    if fact["kind"] in {"TransactionStarted", "TransactionEnded", "MeterSampleObserved"}:
        if not isinstance(data.get("transaction_id"), str) or not data["transaction_id"]:
            raise FactContractError("Transaction evidence requires transaction_id")
    if fact["kind"] == "TransactionStarted":
        if not isinstance(data.get("command_id"), str) or not isinstance(data.get("connector_number"), int) or data["connector_number"] <= 0:
            raise FactContractError("Start requires exact command and connector")
        UUID(data["command_id"])
    if fact["kind"] == "MeterSampleObserved":
        if type(data.get("energy_wh")) is not int or data["energy_wh"] < 0:
            raise FactContractError("Meter evidence requires nonnegative integer Wh")
    if fact["kind"].startswith("Command"):
        if not isinstance(data.get("command_id"), str):
            raise FactContractError("Command result requires command id")
        UUID(data["command_id"])
    return fact


def make_command(command_id, charger_identity, kind, params):
    if kind not in COMMANDS:
        raise FactContractError("Unknown semantic HAL command")
    return {"contract":VERSION,"command_id":str(command_id), "charger_identity":charger_identity,
            "kind":kind,"parameters":params}
