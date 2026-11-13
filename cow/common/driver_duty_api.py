"""C3 shared, typed Driver/Admin duty HTTP response contract."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field



class Strict(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ScheduleView(Strict):
    id: UUID
    driver_id: UUID
    vehicle_id: UUID
    charger_id: UUID
    starts_at: datetime
    ends_at: datetime
    status: str
    version: int
    created_at: datetime


class ScheduleEnvelope(Strict):
    data: ScheduleView


class SchedulePage(Strict):
    items: list[ScheduleView]


class SchedulePageEnvelope(Strict):
    data: SchedulePage


class DutyView(Strict):
    id: UUID
    driver_id: UUID
    vehicle_id: UUID
    charger_id: UUID
    schedule_id: UUID | None
    state: str
    version: int
    started_at: datetime
    ended_at: datetime | None
    dispatchable: bool
    unavailable_reasons: list[str]


class DutyEnvelope(Strict):
    data: DutyView


class CurrentDutyEnvelope(Strict):
    data: DutyView | None


class StartInput(Strict):
    schedule_id: UUID


class ReasonInput(Strict):
    reason: str = Field(min_length=8, max_length=500)


class BreakView(Strict):
    break_id: UUID
    duty: DutyView


class BreakEnvelope(Strict):
    data: BreakView


class EligibleAsset(Strict):
    vehicle_id: UUID
    charger_id: UUID


class EligibleAssets(Strict):
    eligible: bool
    reason: str
    items: list[EligibleAsset]


class EligibleAssetsEnvelope(Strict):
    data: EligibleAssets


class MismatchInput(Strict):
    observed_vehicle_id: UUID
    observed_charger_id: UUID
    reason: str = Field(min_length=8, max_length=500)


class MismatchView(Strict):
    mismatch_id: UUID
    duty: DutyView


class MismatchEnvelope(Strict):
    data: MismatchView
