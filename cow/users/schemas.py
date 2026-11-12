"""Pydantic and Ninja schemas for the Customer / User plane (/api/v1/app).

These schemas define the request bodies and response envelopes for all customer-facing
APIs: Profile, Saved EVs, Saved Addresses, Service Requests, Jobs, Ratings, Support
Tickets, Notifications, and Realtime SSE Tickets.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel as Schema, ConfigDict, Field

from cow.users.contracts import (
    AddressCreate,
    AddressFields,
    AddressPatch,
    ProfilePatch,
    Strict,
    VehicleCreate,
    VehicleFields,
    VehiclePatch,
    merged_address,
    merged_vehicle,
)

__all__ = [
    # Re-exported contract inputs
    "Strict",
    "ProfilePatch",
    "VehicleFields",
    "VehicleCreate",
    "VehiclePatch",
    "AddressFields",
    "AddressCreate",
    "AddressPatch",
    "merged_vehicle",
    "merged_address",
    # Profile DTOs
    "ProfileData",
    "ProfileEnvelope",
    # Vehicle DTOs
    "VehicleData",
    "VehicleEnvelope",
    "VehicleListEnvelope",
    # Address DTOs
    "AddressData",
    "AddressEnvelope",
    "AddressListEnvelope",
    # Service Request DTOs
    "ServiceRequestPayload",
    "ServiceRequestData",
    "ServiceRequestEnvelope",
    "ServiceRequestListEnvelope",
    # Job DTOs
    "JobDetailData",
    "JobDetailEnvelope",
    "JobListEnvelope",
    "JobCancelPayload",
    "JobCancelEnvelope",
    "JobLiveProjectionData",
    "JobLiveEnvelope",
    # Snapshot Promotion DTOs
    "PromoteVehiclePayload",
    "PromoteAddressPayload",
    # Rating DTOs
    "RatingCreatePayload",
    "RatingData",
    "RatingEnvelope",
    "RatingListEnvelope",
    # Support DTOs
    "SupportTicketCreatePayload",
    "SupportReplyPayload",
    "SupportTicketReplyData",
    "SupportTicketData",
    "SupportTicketEnvelope",
    "SupportTicketListEnvelope",
    # Notification DTOs
    "NotificationData",
    "NotificationEnvelope",
    "NotificationListEnvelope",
    "NotificationUnreadCountData",
    "NotificationUnreadCountEnvelope",
    "NotificationPreferencesPayload",
    "NotificationPreferencesData",
    "NotificationPreferencesEnvelope",
    # Realtime Ticket DTOs
    "RealtimeTicketPayload",
    "RealtimeTicketData",
    "RealtimeTicketEnvelope",
]


# ============================================================================
# Profile Schemas
# ============================================================================

class ProfileData(Schema):
    model_config = ConfigDict(extra="forbid")
    full_name: str
    phone: str | None = None
    version: int


class ProfileEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: ProfileData


# ============================================================================
# Vehicle Schemas
# ============================================================================

class VehicleData(Schema):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = ""
    make: str
    model: str
    variant: str = ""
    registration_number: str = ""
    battery_capacity_wh: int | None = None
    connector_types: list[str] = Field(default_factory=list)
    supports_ac: bool = False
    supports_dc: bool = False
    max_ac_power_w: int | None = None
    max_dc_power_w: int | None = None
    active: bool = True
    saved: bool = True
    version: int
    capability_evidence: str = "UNVERIFIED_CUSTOMER_DATA"


class VehicleEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: VehicleData


class VehicleListEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: list[VehicleData]


# ============================================================================
# Address Schemas
# ============================================================================

class AddressData(Schema):
    model_config = ConfigDict(extra="forbid")
    id: str
    address: str
    latitude: str
    longitude: str
    label: str = ""
    instructions: str = ""
    line1: str = ""
    line2: str = ""
    locality: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country_code: str = "IN"
    landmark: str = ""
    floor: str = ""
    contact_instructions: str = ""
    geocode_provider: str = ""
    geocode_provider_place_id: str = ""
    active: bool = True
    is_default: bool = False
    saved: bool = True
    version: int


class AddressEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: AddressData


class AddressListEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: list[AddressData]


# ============================================================================
# Service Request Schemas
# ============================================================================

class ServiceRequestPayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    vehicle_id: UUID
    address_id: UUID
    requested_energy_wh: int = Field(gt=0, le=500000)


class ServiceRequestData(Schema):
    model_config = ConfigDict(extra="forbid")
    id: str
    state: str
    vehicle_snapshot: dict[str, Any]
    address_snapshot: dict[str, Any]
    requested_energy_wh: int
    created_at: str | None = None


class ServiceRequestEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: ServiceRequestData


class ServiceRequestListEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: list[ServiceRequestData]


# ============================================================================
# Service Job Schemas
# ============================================================================

class JobDetailData(Schema):
    model_config = ConfigDict(extra="forbid")
    id: str
    state: str
    version: int
    vehicle_snapshot: dict[str, Any]
    address_snapshot: dict[str, Any]
    requested_energy_wh: int | None = None
    quoted_total_paise: int | None = None
    currency: str = "INR"
    driver: dict[str, Any] | None = None
    created_at: str | None = None
    finalized_at: str | None = None


class JobDetailEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: JobDetailData


class JobListEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: list[JobDetailData]


class JobCancelPayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str | None = Field(default=None, max_length=255)


class JobCancelEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: JobDetailData


class JobLiveProjectionData(Schema):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    state: str
    eta_minutes: int | None = None
    driver_location: dict[str, Any] | None = None
    charging_status: dict[str, Any] | None = None
    freshness_timestamp: str


class JobLiveEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: JobLiveProjectionData


# ============================================================================
# Snapshot Promotion Schemas
# ============================================================================

class PromoteVehiclePayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str | None = Field(default=None, max_length=100)


class PromoteAddressPayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str | None = Field(default=None, max_length=80)
    make_default: bool = False


# ============================================================================
# Rating Schemas
# ============================================================================

class RatingCreatePayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)


class RatingData(Schema):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    rating: int
    comment: str | None = None
    created_at: str


class RatingEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: RatingData


class RatingListEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: list[RatingData]


# ============================================================================
# Customer Support Ticket Schemas
# ============================================================================

class SupportTicketCreatePayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: str = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    job_id: UUID | None = None


class SupportReplyPayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=2000)


class SupportTicketReplyData(Schema):
    model_config = ConfigDict(extra="forbid")
    id: str
    author: str = "CUSTOMER"
    message: str
    created_at: str


class SupportTicketData(Schema):
    model_config = ConfigDict(extra="forbid")
    id: str
    channel: str = "CUSTOMER_PLATFORM"
    status: str = "OPEN"
    category: str
    subject: str
    job_id: str | None = None
    messages: list[SupportTicketReplyData] = Field(default_factory=list)
    created_at: str
    updated_at: str


class SupportTicketEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: SupportTicketData


class SupportTicketListEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: list[SupportTicketData]


# ============================================================================
# Notification & Preference Schemas
# ============================================================================

class NotificationData(Schema):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    body: str
    category: str
    is_read: bool = False
    created_at: str


class NotificationEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: NotificationData


class NotificationListEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: list[NotificationData]


class NotificationUnreadCountData(Schema):
    model_config = ConfigDict(extra="forbid")
    unread_count: int


class NotificationUnreadCountEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: NotificationUnreadCountData


class NotificationPreferencesPayload(Schema):
    model_config = ConfigDict(extra="forbid")
    push_enabled: bool | None = None
    email_enabled: bool | None = None
    sms_enabled: bool | None = None
    service_updates: bool | None = None
    promotions: bool | None = None


class NotificationPreferencesData(Schema):
    model_config = ConfigDict(extra="forbid")
    push_enabled: bool = True
    email_enabled: bool = True
    sms_enabled: bool = True
    service_updates: bool = True
    promotions: bool = False


class NotificationPreferencesEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: NotificationPreferencesData


# ============================================================================
# Realtime SSE Ticket Schemas
# ============================================================================

class RealtimeTicketPayload(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subscriptions: list[str] = Field(default_factory=lambda: ["self", "notifications"])
    resume_after: str | None = None


class RealtimeTicketData(Schema):
    model_config = ConfigDict(extra="forbid")
    ticket: str
    expires_at: str
    stream_url: str


class RealtimeTicketEnvelope(Schema):
    model_config = ConfigDict(extra="forbid")
    data: RealtimeTicketData
