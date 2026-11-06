"""Sprint 7 Admin Zone, assignment, GST and composite-tariff HTTP controls."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from django.db import IntegrityError
from django.http import JsonResponse
from ninja import Query, Schema
from pydantic import ConfigDict, Field

from cow.common.errors import reject
from cow.models import PricingPolicy
from cow.services import idempotency, pricing_admin, rbac, zone_admin
from cow.services.pricing_wire import InvalidPricingBody, check_body
from cow.admin.controls import _json, _key, _version
from cow.admin.zone_contracts import (
    ZoneEnvelope, ZoneListEnvelope, AssignmentEnvelope, AssignmentListEnvelope,
    GSTEnvelope, GSTListEnvelope, TariffEnvelope, TariffListEnvelope,
    PreviewEnvelope, ZoneTariffPlan, TaxProfile,
)


class Strict(Schema):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Reason(Strict):
    reason: str = Field(min_length=8, max_length=500)


class ZoneDraft(Reason):
    zone_key: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1, le=2**31-1, strict=True)
    name: str = Field(min_length=1, max_length=160)
    center_latitude: str = Field(min_length=1, max_length=32)
    center_longitude: str = Field(min_length=1, max_length=32)
    radius_m: int = Field(ge=1, le=100000000, strict=True)
    restriction_mode: Literal['STRICT', 'ADVISORY']
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class AssignmentDraft(Reason):
    zone_id: UUID
    owner_id: UUID
    starts_at: datetime
    ends_at: datetime | None = None
    authorization_ref: str = Field(min_length=1, max_length=160)


class GSTDraft(Reason):
    zone_id: UUID
    channel: Literal['ROAMING', 'ON_SITE_QR']
    binding_version: int = Field(ge=1, strict=True)
    starts_at: datetime
    ends_at: datetime | None = None
    seller_tax_ref: str = Field(min_length=1, max_length=200)
    legal_decision_ref: str = Field(min_length=1, max_length=200)
    tax_profiles: dict[str, TaxProfile]
    authorization_ref: str = Field(min_length=1, max_length=160)


class TariffDraft(Reason):
    zone_id: UUID
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    plan: ZoneTariffPlan


class PreviewScenario(Strict):
    channel: Literal['ROAMING', 'ON_SITE_QR']
    duty_id: UUID | None = None
    charger_id: UUID | None = None
    at: datetime | None = None
    latitude: str = Field(min_length=1, max_length=32)
    longitude: str = Field(min_length=1, max_length=32)
    accuracy_m: str = Field(min_length=1, max_length=32)


def _mutate(request, route, payload, service, *, status=201):
    # Domain-write routes reject duplicate keys before trusting Pydantic's
    # already-parsed request body; keep the 32 KiB global JSON bound.
    _json(request)
    try:
        check_body(request.body)
    except InvalidPricingBody:
        reject(422, 'VALIDATION_FAILED', 'Invalid or ambiguous JSON body')
    key = _key(request)
    try:
        return idempotency.execute(
            request.auth, route, key, payload.model_dump(mode='json', exclude_unset=True),
            lambda: (status, {'data': service()}),
        )
    except IntegrityError:
        reject(409, 'ZONE_CONSTRAINT_CONFLICT', 'Zone mutation conflicts with durable database constraints')


def register(router):
    @router.post('/zones/preview', response={200: PreviewEnvelope}, exclude_unset=True, operation_id='adminZoneScenarioPreview')
    def scenario(request, payload: PreviewScenario):
        # No stored quotation, no wallet, no HAL, no customer-authoritative proof.
        _json(request)
        return {'data': zone_admin.preview_scenario(request.auth, payload.model_dump(exclude_unset=True))}

    @router.get('/zones', operation_id='adminZonesList', response=ZoneListEnvelope)
    def list_zones(request, status: str = '', limit: int = Query(50, ge=1, le=100)):
        return {'data': zone_admin.list_zones(request.auth, status, limit)}

    @router.post('/zones', response={201: ZoneEnvelope, 200: ZoneEnvelope}, operation_id='adminZoneCreate')
    def create_zone(request, payload: ZoneDraft):
        return _mutate(request, 'POST /api/v1/admin/zones', payload,
                       lambda: zone_admin.create_zone(request.auth, payload.model_dump(exclude_unset=True)))

    @router.get('/zones/{zone_id}', operation_id='adminZoneGet', response=ZoneEnvelope)
    def get_zone(request, zone_id: UUID):
        return {'data': zone_admin.get_zone(request.auth, zone_id)}

    @router.post('/zones/{zone_id}/publish', response={200: ZoneEnvelope}, operation_id='adminZonePublish')
    def publish_zone(request, zone_id: UUID, payload: Reason):
        return _mutate(request, f'POST /api/v1/admin/zones/{zone_id}/publish', payload,
                       lambda: zone_admin.transition_zone(request.auth, zone_id, 'publish', payload.reason), status=200)

    @router.post('/zones/{zone_id}/retire', response={200: ZoneEnvelope}, operation_id='adminZoneRetire')
    def retire_zone(request, zone_id: UUID, payload: Reason):
        return _mutate(request, f'POST /api/v1/admin/zones/{zone_id}/retire', payload,
                       lambda: zone_admin.transition_zone(request.auth, zone_id, 'retire', payload.reason), status=200)

    @router.get('/zones/{zone_id}/assignments', operation_id='adminZoneAssignmentsList', response=AssignmentListEnvelope)
    def list_assignments(request, zone_id: UUID):
        return {'data': zone_admin.list_assignments(request.auth, zone_id)}

    @router.post('/zones/assignments/{kind}', response={201: AssignmentEnvelope, 200: AssignmentEnvelope}, operation_id='adminZoneAssign')
    def assign(request, kind: Literal['duty', 'charger'], payload: AssignmentDraft):
        return _mutate(request, f'POST /api/v1/admin/zones/assignments/{kind}', payload,
                       lambda: zone_admin.assign_zone(request.auth, kind, payload.model_dump(exclude_unset=True)))

    @router.post('/zones/assignments/{kind}/{assignment_id}/close', response={200: AssignmentEnvelope}, operation_id='adminZoneAssignmentClose')
    def close_assignment(request, kind: Literal['duty', 'charger'], assignment_id: UUID, payload: Reason):
        return _mutate(request, f'POST /api/v1/admin/zones/assignments/{kind}/{assignment_id}/close', payload,
                       lambda: zone_admin.close_assignment(request.auth, kind, assignment_id, payload.reason), status=200)

    @router.get('/zones/{zone_id}/gst', operation_id='adminZoneGSTList', response=GSTListEnvelope)
    def list_gst(request, zone_id: UUID):
        return {'data': zone_admin.list_gst(request.auth, zone_id)}

    @router.post('/zones/gst', response={201: GSTEnvelope, 200: GSTEnvelope}, operation_id='adminZoneGSTDraft')
    def create_gst(request, payload: GSTDraft):
        return _mutate(request, 'POST /api/v1/admin/zones/gst', payload,
                       lambda: zone_admin.create_gst(request.auth, payload.model_dump(exclude_unset=True)))

    @router.post('/zones/gst/{gst_id}/approve', response={200: GSTEnvelope}, operation_id='adminZoneGSTApprove')
    def approve_gst(request, gst_id: UUID, payload: Reason):
        return _mutate(request, f'POST /api/v1/admin/zones/gst/{gst_id}/approve', payload,
                       lambda: zone_admin.approve_gst(request.auth, gst_id, payload.reason), status=200)

    @router.post('/zones/gst/{gst_id}/close', response={200: GSTEnvelope}, operation_id='adminZoneGSTClose')
    def close_gst(request, gst_id: UUID, payload: Reason):
        return _mutate(request, f'POST /api/v1/admin/zones/gst/{gst_id}/close', payload,
                       lambda: zone_admin.close_gst(request.auth, gst_id, payload.reason), status=200)

    @router.get('/zones/{zone_id}/tariffs', operation_id='adminZoneTariffsList', response=TariffListEnvelope, exclude_unset=True)
    def list_tariffs(request, zone_id: UUID):
        return {'data': zone_admin.list_tariffs(request.auth, zone_id)}

    @router.post('/zones/tariffs', response={201: TariffEnvelope, 200: TariffEnvelope}, exclude_unset=True, operation_id='adminZoneTariffDraft')
    def create_tariff(request, payload: TariffDraft):
        status, body = _mutate(request, 'POST /api/v1/admin/zones/tariffs', payload,
                              lambda: zone_admin.create_tariff(request.auth, payload.model_dump(exclude_unset=True)))
        response = JsonResponse(body, status=status)
        response['ETag'] = f'"{body["data"]["policy"]["state_version"]}"'
        return response

    @router.get('/zones/tariffs/{policy_id}', operation_id='adminZoneTariffGet', response=TariffEnvelope, exclude_unset=True)
    def get_tariff(request, policy_id: UUID):
        data = zone_admin.get_tariff(request.auth, policy_id)
        response = JsonResponse({'data': data})
        response['ETag'] = f'"{data["policy"]["state_version"]}"'
        return response

    def policy_transition(request, policy_id, payload, action):
        rbac.require(request.auth, 'pricing.manage')
        if not PricingPolicy.objects.filter(pk=policy_id, scope__kind__in=('ZONE_ROAMING', 'ZONE_ON_SITE_QR')).exists():
            reject(404, 'ZONE_TARIFF_NOT_FOUND', 'Zone pricing revision not found')
        _json(request)
        _version_value = _version(request)
        def do_transition():
            func = pricing_admin.publish if action == 'publish' else pricing_admin.withdraw
            result = func(request.auth, policy_id, _version_value, payload.reason)
            return zone_admin.tariff_view(result)
        status, body = _mutate(request, f'POST /api/v1/admin/zones/tariffs/{policy_id}/{action} {_version_value}',
                               payload, do_transition, status=200)
        response = JsonResponse(body, status=status)
        response['ETag'] = f'"{body["data"]["policy"]["state_version"]}"'
        return response

    @router.post('/zones/tariffs/{policy_id}/publish', response={200: TariffEnvelope}, exclude_unset=True, operation_id='adminZoneTariffPublish')
    def publish_tariff(request, policy_id: UUID, payload: Reason):
        return policy_transition(request, policy_id, payload, 'publish')

    @router.post('/zones/tariffs/{policy_id}/withdraw', response={200: TariffEnvelope}, exclude_unset=True, operation_id='adminZoneTariffWithdraw')
    def withdraw_tariff(request, policy_id: UUID, payload: Reason):
        return policy_transition(request, policy_id, payload, 'withdraw')
