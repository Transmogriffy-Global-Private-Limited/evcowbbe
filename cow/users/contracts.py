"""Customer-owned master data: input validation, never charger capability certification."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfilePatch(Strict):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    phone: str | None = Field(default=None, max_length=24)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("At least one profile field is required")
        if "full_name" in self.model_fields_set and self.full_name is None:
            raise ValueError("full_name cannot be null")
        if "phone" in self.model_fields_set and self.phone is not None:
            if self.phone and (not self.phone.startswith("+") or not self.phone[1:].isdigit()):
                raise ValueError("Phone must be E.164-like or empty")
        return self


class VehicleFields(Strict):
    label: str = Field(default="", max_length=100)
    make: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    variant: str = Field(default="", max_length=120)
    registration_number: str = Field(default="", max_length=32)
    battery_capacity_wh: int | None = Field(default=None, gt=0, le=500_000_000, strict=True)
    connector_types: list[str] = Field(default_factory=list, max_length=16)
    supports_ac: bool = Field(default=False, strict=True)
    supports_dc: bool = Field(default=False, strict=True)
    max_ac_power_w: int | None = Field(default=None, gt=0, strict=True)
    max_dc_power_w: int | None = Field(default=None, gt=0, strict=True)
    saved: bool = Field(default=True, strict=True)

    @field_validator("connector_types")
    @classmethod
    def normalize_connectors(cls, values):
        if any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 60 for v in values):
            raise ValueError("Connector labels must be bounded strings")
        cleaned = [v.strip().upper() for v in values]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("Duplicate connector labels")
        return cleaned

    @model_validator(mode="after")
    def consistency(self):
        if not (self.supports_ac or self.supports_dc):
            raise ValueError("At least one charging mode must be explicitly self-reported")
        if self.max_ac_power_w is not None and not self.supports_ac:
            raise ValueError("AC power requires AC support")
        if self.max_dc_power_w is not None and not self.supports_dc:
            raise ValueError("DC power requires DC support")
        return self


class VehicleCreate(VehicleFields):
    pass


class VehiclePatch(Strict):
    label: str | None = Field(default=None, max_length=100)
    make: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    variant: str | None = Field(default=None, max_length=120)
    registration_number: str | None = Field(default=None, max_length=32)
    battery_capacity_wh: int | None = Field(default=None, gt=0, le=500_000_000, strict=True)
    connector_types: list[str] | None = Field(default=None, max_length=16)
    supports_ac: bool | None = Field(default=None, strict=True)
    supports_dc: bool | None = Field(default=None, strict=True)
    max_ac_power_w: int | None = Field(default=None, gt=0, strict=True)
    max_dc_power_w: int | None = Field(default=None, gt=0, strict=True)
    saved: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("At least one vehicle field required")
        required = self.model_fields_set - {
            "battery_capacity_wh", "max_ac_power_w", "max_dc_power_w"
        }
        if any(getattr(self, name) is None for name in required):
            raise ValueError("Non-nullable vehicle field cannot be null")
        return self


def merged_vehicle(existing: dict, patch: VehiclePatch) -> VehicleFields:
    return VehicleFields.model_validate({**existing, **patch.model_dump(exclude_unset=True)})


Coordinate = Annotated[Decimal, Field(max_digits=10, decimal_places=7)]


class AddressFields(Strict):
    address: str = Field(min_length=5, max_length=700)
    latitude: Coordinate = Field(ge=Decimal("-90"), le=Decimal("90"))
    longitude: Coordinate = Field(ge=Decimal("-180"), le=Decimal("180"))
    label: str = Field(default="", max_length=80)
    instructions: str = Field(default="", max_length=1000)
    line1: str = Field(default="", max_length=250)
    line2: str = Field(default="", max_length=250)
    locality: str = Field(default="", max_length=150)
    city: str = Field(default="", max_length=120)
    state: str = Field(default="", max_length=120)
    postal_code: str = Field(default="", max_length=24)
    country_code: str = Field(default="IN", min_length=2, max_length=2)
    landmark: str = Field(default="", max_length=160)
    floor: str = Field(default="", max_length=60)
    contact_instructions: str = Field(default="", max_length=500)
    geocode_provider: str = Field(default="", max_length=80)
    geocode_provider_place_id: str = Field(default="", max_length=200)
    saved: bool = Field(default=True, strict=True)

    @field_validator("country_code")
    @classmethod
    def country_code_uppercase(cls, value):
        if not value.isalpha() or not value.isupper():
            raise ValueError("Expected uppercase ISO-like country code")
        return value


class AddressCreate(AddressFields):
    pass


class AddressPatch(Strict):
    address: str | None = Field(default=None, min_length=5, max_length=700)
    latitude: Coordinate | None = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
    longitude: Coordinate | None = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))
    label: str | None = Field(default=None, max_length=80)
    instructions: str | None = Field(default=None, max_length=1000)
    line1: str | None = Field(default=None, max_length=250)
    line2: str | None = Field(default=None, max_length=250)
    locality: str | None = Field(default=None, max_length=150)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=24)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    landmark: str | None = Field(default=None, max_length=160)
    floor: str | None = Field(default=None, max_length=60)
    contact_instructions: str | None = Field(default=None, max_length=500)
    geocode_provider: str | None = Field(default=None, max_length=80)
    geocode_provider_place_id: str | None = Field(default=None, max_length=200)
    saved: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("At least one address field required")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Address fields cannot be null")
        return self


def merged_address(existing: dict, patch: AddressPatch) -> AddressFields:
    return AddressFields.model_validate({**existing, **patch.model_dump(exclude_unset=True)})
