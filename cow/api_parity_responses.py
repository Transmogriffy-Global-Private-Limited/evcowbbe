"""Actual Django fleet/pricing service output shapes (source-derived DTOs).

Declarative DTOs. Django JsonResponse intentionally bypasses Ninja serialization;
use explicit contract tests and differential tests before claiming parity.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Vehicle(_Strict):
    id: UUID
    asset_tag: str
    registration_number: str | None
    make: str
    model: str
    variant: str | None
    vehicle_type: str | None
    ownership_type: str | None
    registration_valid_until: date | None
    insurance_valid_until: date | None
    inspection_status: str
    operational_status: str
    maintenance_status: str
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class Charger(_Strict):
    id: UUID
    asset_tag: str
    serial_number: str | None
    manufacturer: str
    model: str
    rated_power_w: int | None
    protocol_family: str | None
    protocol_version: str | None
    hal_identity: str | None
    operational_status: str
    maintenance_status: str
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class Connector(_Strict):
    id: UUID
    charger_id: UUID
    connector_number: int = Field(ge=1, le=2147483647)
    connector_type: str
    max_power_w: int = Field(ge=1, le=2147483647)
    supports_ac: bool
    supports_dc: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class Maintenance(_Strict):
    id: UUID
    starts_at: datetime
    ends_at: datetime
    reason: str
    created_by: UUID | None
    created_at: datetime
    vehicle_id: UUID | None = None
    charger_id: UUID | None = None


class Pairing(_Strict):
    id: UUID
    vehicle_id: UUID
    charger_id: UUID
    effective_from: datetime
    effective_to: datetime | None
    status: Literal["ACTIVE", "ENDED", "INVALIDATED"]
    source: str
    created_by: UUID | None
    created_at: datetime | None
    reason: str
    end_reason: str | None
    ended_by: UUID | None


class Availability(_Strict):
    asset_id: str
    inventory_state: str
    active_pairings: int = Field(ge=0)
    maintenance_now: bool
    dispatch_eligibility: str
    hal_connectivity: str


class HistoryEvent(_Strict):
    # Some audit rows may contain optional actor/asset IDs or arbitrary
    # before/after values. Do not invent historical actor identity.
    id: UUID
    actor_id: UUID | None = None
    asset_kind: str
    asset_id: str
    action: str
    reason: str = ""
    before_value: Any = None
    after_value: Any = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class PricingPolicy(_Strict):
    id: UUID
    policy_key: str
    policy_version: int = Field(ge=1)
    scope: dict[str, Any]
    currency: str
    rules: list[dict[str, Any]]
    starts_at: datetime | None
    ends_at: datetime | None
    status: str
    state_version: int = Field(ge=1)
    created_at: datetime


class PricingHistoryEvent(_Strict):
    id: UUID
    action: str
    occurred_at: datetime
    details: dict[str, Any]


class VehicleEnvelope(_Strict):
    data: Vehicle


class ChargerEnvelope(_Strict):
    data: Charger


class ConnectorEnvelope(_Strict):
    data: Connector


class PairingEnvelope(_Strict):
    data: Pairing


class MaintenanceEnvelope(_Strict):
    data: Maintenance


class AvailabilityEnvelope(_Strict):
    data: Availability


class VehiclePage(_Strict):
    items: list[Vehicle] = Field(max_length=100)
    next_cursor: UUID = None  # omission is allowed; explicit null is not emitted


class ChargerPage(_Strict):
    items: list[Charger] = Field(max_length=100)
    next_cursor: UUID = None


class ConnectorPage(_Strict):
    items: list[Connector] = Field(max_length=100)


class PairingPage(_Strict):
    items: list[Pairing] = Field(max_length=100)


class HistoryPage(_Strict):
    items: list[HistoryEvent] = Field(max_length=100)


class VehiclePageEnvelope(_Strict):
    data: VehiclePage


class ChargerPageEnvelope(_Strict):
    data: ChargerPage


class ConnectorPageEnvelope(_Strict):
    data: ConnectorPage


class PairingPageEnvelope(_Strict):
    data: PairingPage


class HistoryPageEnvelope(_Strict):
    data: HistoryPage


class PricingEnvelope(_Strict):
    data: PricingPolicy


class PricingListEnvelope(_Strict):
    data: list[PricingPolicy] = Field(max_length=100)


class PricingHistoryEnvelope(_Strict):
    data: list[PricingHistoryEvent] = Field(max_length=100)
