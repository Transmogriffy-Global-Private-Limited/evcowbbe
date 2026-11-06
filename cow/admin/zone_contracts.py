"""Public Admin Zone API contracts; JSON responses mirror zone_admin projections.

Only Zone-owned composite plans are customer-facing tariff configuration.
Amounts use integer INR minor units; rates are exact decimal strings.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field


class Strict(Schema):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


Channel = Literal['ROAMING', 'ON_SITE_QR']
Phase = Literal['UPFRONT', 'COMPLETED', 'CANCELLED', 'NO_SHOW']
Rounding = Literal['CEIL_STARTED_MINUTE', 'FLOOR_COMPLETED_MINUTE']


class ChargingComponent(Strict):
    basis: Literal['ENERGY_KWH', 'TIME_MINUTE', 'SESSION']
    rate: str = Field(description='Exact nonnegative INR decimal rate, not floating-point money')
    tax_class: str
    minute_rounding: Rounding | None = None


class TransportComponent(Strict):
    rate: str
    tax_class: str
    applies_on: list[Phase] = Field(min_length=1)


class DistanceSlab(Strict):
    up_to_m: int | None = Field(description='Cumulative distance boundary in meters; last slab must be null')
    rate_per_km: str


class DistanceComponent(Strict):
    minimum_billable_m: int = Field(ge=0)
    slabs: list[DistanceSlab] = Field(min_length=1, max_length=32)
    tax_class: str
    applies_on: list[Phase] = Field(min_length=1)


class ExtraComponent(Strict):
    id: str
    basis: Literal['FIXED', 'WAITING_MINUTE']
    rate: str
    tax_class: str
    applies_on: list[Phase] = Field(min_length=1)
    minute_rounding: Rounding | None = None


class TariffProfile(Strict):
    charging: ChargingComponent
    transport: TransportComponent
    distance: DistanceComponent
    extras: list[ExtraComponent] = Field(max_length=48)


class TariffProfiles(Strict):
    INSIDE: TariffProfile
    OUTSIDE: TariffProfile | None = None


class ZoneTariffPlan(Strict):
    policy_id: str = Field(description='Globally versioned uppercase policy identifier')
    policy_version: int = Field(ge=1, strict=True)
    zone_id: str = Field(description='Published ServiceZone.zone_key, NOT the Zone UUID')
    channel: Channel
    currency: Literal['INR']
    profiles: TariffProfiles


class TaxProfile(Strict):
    approved: Literal[True]
    sgst_percent: str
    cgst_percent: str
    igst_percent: str


class ZoneData(Strict):
    id: UUID
    zone_key: str
    version: int
    name: str
    center_latitude: str
    center_longitude: str
    radius_m: int
    restriction_mode: Literal['STRICT', 'ADVISORY']
    status: Literal['DRAFT', 'PUBLISHED', 'RETIRED']
    starts_at: datetime | None
    ends_at: datetime | None
    created_by_id: UUID


class ZoneEnvelope(Strict):
    data: ZoneData


class ZoneListEnvelope(Strict):
    data: list[ZoneData]


class AssignmentData(Strict):
    id: UUID
    kind: Literal['duty', 'charger']
    zone_id: UUID
    owner_id: UUID
    starts_at: datetime
    ends_at: datetime | None
    authorization_ref: str


class AssignmentEnvelope(Strict):
    data: AssignmentData


class AssignmentListEnvelope(Strict):
    data: list[AssignmentData]


class GSTData(Strict):
    id: UUID
    zone_id: UUID
    channel: Channel
    binding_version: int
    status: Literal['DRAFT', 'APPROVED']
    starts_at: datetime
    ends_at: datetime | None
    seller_tax_ref: str
    legal_decision_ref: str
    tax_profiles: dict[str, TaxProfile]
    authorization_ref: str


class GSTEnvelope(Strict):
    data: GSTData


class GSTListEnvelope(Strict):
    data: list[GSTData]


class PricingLedgerData(Strict):
    id: UUID
    policy_key: str
    policy_version: int
    scope: dict[str, str]
    currency: Literal['INR']
    rules: list[dict[str, Any]] = Field(description='Empty for Zone composite tariffs; plan owns the charges')
    starts_at: datetime | None
    ends_at: datetime | None
    status: Literal['DRAFT', 'PUBLISHED', 'WITHDRAWN']
    state_version: int
    created_at: datetime


class TariffData(Strict):
    policy: PricingLedgerData
    zone_id: UUID
    channel: Channel
    plan: ZoneTariffPlan


class TariffEnvelope(Strict):
    data: TariffData


class TariffListEnvelope(Strict):
    data: list[TariffData]


class PriceLine(Strict):
    rule_id: str
    component: str
    kind: Literal['BASE', 'TAX', 'OVERRIDE_RATE', 'MULTIPLIER', 'DISCOUNT_PERCENT']
    rate: str
    amount_minor: int
    tax_class: str
    basis: str | None = None
    quantity: str | None = None
    source: str | None = None
    estimate: bool | None = None
    target_rule_id: str | None = None


class CommercialContextData(Strict):
    zone_id: str
    channel: Channel
    relation: Literal['INSIDE', 'OUTSIDE']
    decision_at: datetime
    provenance: str
    driver_duty_id: str | None
    site_id: str | None


class TaxBindingData(Strict):
    channel: Channel
    version: int
    seller_tax_ref: str
    legal_decision_ref: str
    tax_profiles: dict[str, TaxProfile]


class ZonePricePreview(Strict):
    policy_id: str
    policy_version: int
    currency: Literal['INR']
    mode: Literal['PREVIEW']
    estimated: bool
    lines: list[PriceLine]
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    commercial_context: CommercialContextData
    tax_binding: TaxBindingData
    phase: Literal['UPFRONT']
    policy_snapshot: ZoneTariffPlan


class PreviewData(Strict):
    simulation_only: Literal[True]
    may_charge_customer: Literal[False]
    may_accept_quote: Literal[False]
    may_command_hal: Literal[False]
    position_trusted: Literal[False]
    zone: ZoneData
    preview: ZonePricePreview = Field(description='Typed nonbinding price breakdown in integer paise; not a committed quote')


class PreviewEnvelope(Strict):
    data: PreviewData
