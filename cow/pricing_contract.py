"""Pure strict validation for the Admin pricing draft contract (not a billing engine)."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
RATE = re.compile(r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Scope(Strict):
    kind: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=1, max_length=160)

    @field_validator("key")
    @classmethod
    def valid_key(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}",value):
            raise ValueError("Invalid scope key")
        return value

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value):
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("Invalid scope kind")
        return value


class Rule(Strict):
    id: str
    kind: Literal["BASE", "OVERRIDE_RATE", "MULTIPLIER", "DISCOUNT_PERCENT"]
    rate: str
    priority: int = Field(default=0, ge=0, le=100_000, strict=True)
    component: str | None = None
    basis: str | None = None
    target: str | None = None
    tax_class: str | None = None
    conditions: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value):
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("Invalid rule identifier")
        return value

    @field_validator("rate", mode="before")
    @classmethod
    def valid_rate(cls, value):
        if not isinstance(value, str) or not RATE.fullmatch(value):
            raise ValueError("Rate must be an exact INR decimal string")
        return value

    @model_validator(mode="after")
    def valid_shape(self):
        if self.kind == "BASE":
            if not all((self.component, self.basis, self.tax_class)) or self.target is not None:
                raise ValueError("BASE requires component, basis and tax_class without target")
        elif not self.target or not IDENTIFIER.fullmatch(self.target):
            raise ValueError("Modifier requires a BASE target")
        elif any(x is not None for x in (self.component, self.basis, self.tax_class)):
            raise ValueError("Modifiers cannot redefine base dimensions or tax")
        if self.kind == "DISCOUNT_PERCENT" and Decimal(self.rate) > Decimal("100"):
            raise ValueError("Discount percentage exceeds 100")
        return self


class Draft(Strict):
    policy_key: str
    policy_version: int = Field(ge=1, strict=True)
    scope: Scope
    currency: Literal["INR"]
    rules: list[Rule] = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=8, max_length=500)
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @field_validator("policy_key")
    @classmethod
    def valid_policy_key(cls, value):
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("Invalid policy key")
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def aware_utc(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Effective timestamps must have time zone offsets")
            value = value.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.ends_at is not None and self.starts_at is None:
            raise ValueError("ends_at requires starts_at")
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must follow starts_at")
        ids = [r.id for r in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate pricing rule ID")
        base = {r.id for r in self.rules if r.kind == "BASE"}
        if not base:
            raise ValueError("At least one BASE rule is required")
        from cow.services.pricing_engine import validate, PricingError
        try:
            validate({"policy_id": self.policy_key, "policy_version": self.policy_version,
                      "currency": self.currency,
                      "rules": [r.model_dump(exclude_none=True) for r in self.rules]})
        except PricingError as exc:
            raise ValueError(str(exc)) from exc
        return self


def overlap(a_start, a_end, b_start, b_end):
    """Half-open intervals, with None meaning unbounded on its respective side."""
    return (a_end is None or b_start is None or b_start < a_end) and (
        b_end is None or a_start is None or a_start < b_end
    )
