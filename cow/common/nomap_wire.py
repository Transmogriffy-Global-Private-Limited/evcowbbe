"""Versioned, provider-neutral CoW V3 Driver/Admin/Customer API response wire types.

These are public boundary contracts; service modules retain business authority.
Use the `data` envelope for SDK generation. Never expose provider keys, OTP
hashes, wallet HMAC secrets, or internal Django model objects.
"""
from __future__ import annotations
from typing import Any
from ninja import Schema
from pydantic import ConfigDict


class Strict(Schema):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class JobView(Strict):
    id: str
    state: str
    version: int
    request_id: str
    driver_duty_id: str | None
    customer_id: str
    updated_at: str


class JobEnvelope(Strict):
    data: JobView


class JobPageEnvelope(Strict):
    data: list[JobView]


class LocationView(Strict):
    observation_id: str
    sequence: int
    observed_at: str
    received_at: str
    accuracy_m: int
    latitude: str | None
    longitude: str | None
    method: str
    trusted_hardware_location: bool
    replayed: bool | None = None


class LocationEnvelope(Strict):
    data: LocationView


class TrackingView(Strict):
    job_id: str
    tracking_state: str
    latest: LocationView | None
    job_state: str | None = None
    next_poll_seconds: int | None = None
    event_schema: str | None = None
    routing_available: bool | None = None
    road_eta_available: bool | None = None


class TrackingEnvelope(Strict):
    data: TrackingView


class PreviewView(Strict):
    request_id: str
    zone_id: str
    acceptance_token: str
    expires_at: str
    summary: dict[str, Any]
    summary_sha256: str
    distance_method: str
    distance_not_driving_route: bool
    requires_explicit_customer_acceptance: bool


class PreviewEnvelope(Strict):
    data: PreviewView


class OTPView(Strict):
    ticket_id: str
    state: str
    otp: str | None = None
    replayed: bool = False
    expires_at: str
    note: str | None = None


class OTPEnvelope(Strict):
    data: OTPView


class VerifyOTPView(Strict):
    ticket_id: str
    verified: bool
    state: str
    remaining_attempts: int


class VerifyOTPEnvelope(Strict):
    data: VerifyOTPView


class ConsentView(Strict):
    booking_id: str
    ticket_id: str
    handover_token: str
    expires_at: str
    state: str
    proof: str
    replayed: bool = False


class ConsentEnvelope(Strict):
    data: ConsentView


class DriverClosureView(Strict):
    job: JobView
    review_id: str
    state: str
    awaits_admin_approval: bool


class DriverClosureEnvelope(Strict):
    data: DriverClosureView


class AdminClosureView(Strict):
    job: JobView
    review_id: str
    state: str
    completion_ref: str
    settlement_not_posted: bool


class AdminClosureEnvelope(Strict):
    data: AdminClosureView


class WalletRefundView(Strict):
    id: str
    settlement_id: str
    amount_paise: int
    state: str
    requested_by: str
    approved_by: str | None
    wallet_transaction_id: str | None
    refund_type: str


class WalletRefundEnvelope(Strict):
    data: WalletRefundView
