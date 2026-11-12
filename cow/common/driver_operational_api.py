"""Shared C2 operation projection contract for Admin and Driver HTTP roles."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict


class Strict(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OperationalView(Strict):
    driver_id: UUID
    profile_id: UUID | None
    eligibility: Literal["PENDING", "ACTIVE", "SUSPENDED", "OFFBOARDED"]
    version: int | None
    effective_eligible: bool
    reasons: list[str]
    licence_valid_until: date | None
    recruitment_state: str | None
    recruitment_policy_version: int | None
    advisory_read: bool


class OperationalEnvelope(Strict):
    data: OperationalView

