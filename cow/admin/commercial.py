"""Typed, Zone/GST library Admin controls; no customer commitment endpoints.

Register with cow.admin.router.build_router. All mutating routes require the
existing Idempotency-Key helper; tariff publication additionally uses If-Match.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from django.db import IntegrityError
from django.http import JsonResponse
from ninja import Query, Schema
from pydantic import ConfigDict, Field

from cow.admin.controls import _json, _key, _version
from cow.common.errors import reject
from cow.services import commercial_admin, idempotency, rbac
from cow.services.pricing_wire import check_body, InvalidPricingBody


class Strict(Schema):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class Reason(Strict):
    reason: str=Field(min_length=8,max_length=500)


class DecimalRates(Strict):
    CGST: str
    SGST: str
    UTGST: str
    IGST: str


class TaxClass(Strict):
    treatment: Literal['TAXABLE','EXEMPT','NIL_RATED','ZERO_RATED','OUTSIDE_SCOPE']
    supply_group: str
    rates: DecimalRates


class Charging(Strict):
    basis: Literal['ENERGY_KWH','TIME_MINUTE','SESSION']
    rate: str
    tax_class: str
    price_mode: Literal['INCLUSIVE','EXCLUSIVE'] | None=None
    minute_rounding: Literal['CEIL_STARTED_MINUTE','FLOOR_COMPLETED_MINUTE'] | None=None


class Visit(Strict):
    rate: str
    tax_class: str
    price_mode: Literal['INCLUSIVE','EXCLUSIVE'] | None=None
    applies_on: list[Literal['UPFRONT','COMPLETED','CANCELLED','NO_SHOW']]=Field(min_length=1)


class DistanceSlab(Strict):
    up_to_m: int | None=Field(ge=0)
    rate_per_km: str


class Distance(Strict):
    minimum_billable_m: int=Field(ge=0)
    tax_class: str
    price_mode: Literal['INCLUSIVE','EXCLUSIVE'] | None=None
    applies_on: list[Literal['UPFRONT','COMPLETED','CANCELLED','NO_SHOW']]=Field(min_length=1)
    slabs: list[DistanceSlab]=Field(min_length=1,max_length=32)


class Extra(Strict):
    id: str
    basis: Literal['FIXED','WAITING_MINUTE']
    rate: str
    tax_class: str
    applies_on: list[Literal['UPFRONT','COMPLETED','CANCELLED','NO_SHOW']]=Field(min_length=1)
    price_mode: Literal['INCLUSIVE','EXCLUSIVE'] | None=None
    minute_rounding: Literal['CEIL_STARTED_MINUTE','FLOOR_COMPLETED_MINUTE'] | None=None
    grace_seconds: int | None=Field(default=None,ge=0,le=86400)


class Components(Strict):
    charging: Charging
    visit: Visit | None=None
    distance: Distance | None=None
    extras: list[Extra]=Field(default_factory=list,max_length=48)


class Profiles(Strict):
    INSIDE: Components
    OUTSIDE: Components | None=None


class TariffPlan(Strict):
    key: str
    version: int=Field(ge=1,strict=True)
    channel: Literal['ROAMING','ON_SITE_QR']
    currency: Literal['INR']
    price_mode: Literal['INCLUSIVE','EXCLUSIVE']
    profiles: Profiles


class TariffCreate(Reason):
    plan: TariffPlan


class Duplicate(Reason):
    new_key: str=Field(min_length=1,max_length=64)


class DuplicateAssign(Duplicate):
    target_type: Literal['ZONE','GROUP']
    target_id: UUID
    channel: Literal['ROAMING','ON_SITE_QR']


class GroupCreate(Reason):
    name: str=Field(min_length=1,max_length=100)
    description: str=''


class GroupMembership(Reason):
    group_id: UUID


class GSTCreate(Reason):
    key: str=Field(min_length=1,max_length=64)
    version: int=Field(ge=1,strict=True)
    seller_tax_ref: str=Field(min_length=1,max_length=200)
    legal_decision_ref: str=Field(min_length=1,max_length=200)
    tax_classes: dict[str,TaxClass]


class TariffAssignmentCreate(Reason):
    policy_id: UUID
    target_type: Literal['ZONE','GROUP']
    target_id: UUID
    channel: Literal['ROAMING','ON_SITE_QR']
    slot: Literal['DEFAULT','SCHEDULED']='DEFAULT'
    starts_at: datetime | None=None
    ends_at: datetime | None=None


class GSTAssignmentCreate(Reason):
    profile_id: UUID
    zone_id: UUID
    channel: Literal['ROAMING','ON_SITE_QR']
    starts_at: datetime | None=None
    ends_at: datetime | None=None


class ReplaceTariff(Reason):
    new_policy_id: UUID


class ReplaceGST(Reason):
    new_profile_id: UUID


class ChannelChange(Reason):
    enabled: bool


class TariffView(Strict):
    id: UUID
    key: str
    version: int
    status: Literal['DRAFT','PUBLISHED','WITHDRAWN']
    state_version: int
    channel: Literal['ROAMING','ON_SITE_QR']
    plan: TariffPlan
    created_at: datetime


class GSTView(Strict):
    id: UUID
    key: str
    version: int
    status: Literal['DRAFT','APPROVED','RETIRED','REVOKED']
    seller_tax_ref: str
    legal_decision_ref: str
    tax_classes: dict[str,TaxClass]
    created_by_id: UUID
    approved_by_id: UUID | None


class GroupView(Strict):
    id: UUID
    name: str
    description: str
    is_active: bool


class TargetAssignmentView(Strict):
    id: UUID
    policy_id: UUID
    target_type: Literal['ZONE','GROUP']
    target_id: UUID
    channel: Literal['ROAMING','ON_SITE_QR']
    slot: Literal['DEFAULT','SCHEDULED']
    starts_at: datetime | None
    ends_at: datetime | None


class GSTAssignmentView(Strict):
    id: UUID
    zone_id: UUID
    channel: Literal['ROAMING','ON_SITE_QR']
    profile_id: UUID
    starts_at: datetime | None
    ends_at: datetime | None


class ReadinessView(Strict):
    zone_id: UUID
    channel: Literal['ROAMING','ON_SITE_QR']
    enabled: bool
    configuration_ready: bool
    blockers: list[str]
    default_tariff_assignment_id: UUID | None
    gst_assignment_id: UUID | None
    note: str


class ChannelView(Strict):
    zone_id: UUID
    channel: Literal['ROAMING','ON_SITE_QR']
    enabled: bool


class GroupMembershipView(Strict):
    customer_id: UUID
    group_id: UUID
    already_assigned: bool


class DupAssignView(Strict):
    tariff: TariffView
    assignment: TargetAssignmentView


class FareLineView(Strict):
    component: str
    tax_class: str
    supply_group: str
    treatment: str
    quantity: str
    unit: str
    rate: str
    price_mode: Literal['INCLUSIVE','EXCLUSIVE']
    source: str
    estimated: bool
    net_minor: int
    tax_minor: int
    tax_heads_minor: dict[str,int]
    gross_minor: int


class PreviewView(Strict):
    status: Literal['PREVIEW_ONLY']
    engine_version: str
    document_authority: Literal['COMMERCIAL_STATEMENT_ONLY']
    currency: Literal['INR']
    event: Literal['UPFRONT','COMPLETED','CANCELLED','NO_SHOW']
    selection: dict[str,Any]
    lines: list[FareLineView]
    disclosures: list[dict[str,Any]]
    totals: dict[str,int]
    may_collect: Literal[False]
    may_command_hal: Literal[False]
    may_issue_tax_invoice: Literal[False]
    simulation_only: Literal[True]
    may_accept_quote: Literal[False]
    position_trusted: Literal[False]


class TariffEnvelope(Strict):
    data: TariffView
class TariffListEnvelope(Strict):
    data: list[TariffView]
class GSTEnvelope(Strict):
    data: GSTView
class GSTListEnvelope(Strict):
    data: list[GSTView]
class GroupEnvelope(Strict):
    data: GroupView
class GroupListEnvelope(Strict):
    data: list[GroupView]
class GroupMembershipEnvelope(Strict):
    data: GroupMembershipView
class TargetAssignmentEnvelope(Strict):
    data: TargetAssignmentView
class TargetAssignmentListEnvelope(Strict):
    data: list[TargetAssignmentView]
class GSTAssignmentEnvelope(Strict):
    data: GSTAssignmentView
class GSTAssignmentListEnvelope(Strict):
    data: list[GSTAssignmentView]
class DupAssignEnvelope(Strict):
    data: DupAssignView
class ReadinessEnvelope(Strict):
    data: ReadinessView
class ChannelEnvelope(Strict):
    data: ChannelView
class PreviewEnvelope(Strict):
    data: PreviewView


class Reading(Strict):
    value: int=Field(ge=0)
    source: str=Field(min_length=1,max_length=120)


class Preview(Strict):
    zone_id: UUID
    channel: Literal['ROAMING','ON_SITE_QR']
    relation: Literal['INSIDE','OUTSIDE']='INSIDE'
    at: datetime | None=None
    seller_tax_ref: str=Field(min_length=1,max_length=200)
    pricing_group_id: UUID | None=None
    event: Literal['UPFRONT','COMPLETED','CANCELLED','NO_SHOW']
    measurements: dict[str,Reading]=Field(default_factory=dict)


def _mutate(request,route,payload,service,status=201):
    _json(request)
    try:check_body(request.body)
    except InvalidPricingBody:reject(422,'VALIDATION_FAILED','Invalid/ambiguous JSON body')
    key=_key(request)
    try:return idempotency.execute(request.auth,route,key,payload.model_dump(mode='json',exclude_unset=True),lambda:(status,{'data':service()}))
    except IntegrityError:reject(409,'COMMERCIAL_DB_CONFLICT','Commercial write violates durable constraint')


def register(router):
    @router.get('/commercial/tariffs',operation_id='adminCommercialTariffsList',response=TariffListEnvelope)
    def list_tariffs(request, limit:int=Query(50,ge=1,le=100)):
        rbac.require(request.auth,'pricing.read')
        from cow.models import PricingPolicy
        qs=PricingPolicy.objects.filter(scope__kind='LIBRARY_COMPOSITE').select_related('commercial_payload').order_by('-created_at','-id')[:limit]
        return {'data':[commercial_admin.tariff_view(p) for p in qs]}

    @router.post('/commercial/tariffs',operation_id='adminCommercialTariffDraft',response={201:TariffEnvelope,200:TariffEnvelope})
    def create_tariff(request,payload:TariffCreate):
        return _mutate(request,'POST /api/v1/admin/commercial/tariffs',payload,
           lambda:commercial_admin.create_tariff(request.auth,payload.plan.model_dump(mode='json',exclude_none=True),payload.reason))

    @router.get('/commercial/tariffs/{policy_id}',operation_id='adminCommercialTariffGet',response=TariffEnvelope)
    def get_tariff(request,policy_id:UUID):
        rbac.require(request.auth,'pricing.read')
        from cow.models import PricingPolicy
        p=PricingPolicy.objects.select_related('commercial_payload').filter(pk=policy_id,scope__kind='LIBRARY_COMPOSITE').first()
        if p is None:reject(404,'TARIFF_NOT_FOUND','Reusable tariff not found')
        return {'data':commercial_admin.tariff_view(p)}

    @router.post('/commercial/tariffs/{policy_id}/duplicate',operation_id='adminCommercialTariffDuplicate',response={201:TariffEnvelope,200:TariffEnvelope})
    def duplicate_tariff(request,policy_id:UUID,payload:Duplicate):
        return _mutate(request,f'POST /api/v1/admin/commercial/tariffs/{policy_id}/duplicate',payload,
          lambda:commercial_admin.duplicate_tariff(request.auth,policy_id,payload.new_key,payload.reason))

    @router.post('/commercial/tariffs/{policy_id}/publish',operation_id='adminCommercialTariffPublish',response={200:TariffEnvelope})
    def publish_tariff(request,policy_id:UUID,payload:Reason):
        _json(request)
        version=_version(request)
        return _mutate(request,f'POST /api/v1/admin/commercial/tariffs/{policy_id}/publish {version}',payload,
          lambda:commercial_admin.publish_tariff(request.auth,policy_id,version,payload.reason),status=200)

    @router.get('/commercial/groups',operation_id='adminCommercialGroupsList',response=GroupListEnvelope)
    def groups(request,limit:int=Query(50,ge=1,le=100)):
        rbac.require(request.auth,'pricing.read')
        from cow.models import CommercialPricingGroup
        return {'data':[commercial_admin.group_view(x) for x in CommercialPricingGroup.objects.order_by('name')[:limit]]}

    @router.post('/commercial/groups',operation_id='adminCommercialGroupCreate',response={201:GroupEnvelope,200:GroupEnvelope})
    def create_group(request,payload:GroupCreate):
        return _mutate(request,'POST /api/v1/admin/commercial/groups',payload,
            lambda:commercial_admin.create_group(request.auth,payload.name,payload.description,payload.reason))

    @router.post('/commercial/groups/members/{customer_id}',operation_id='adminCommercialGroupMembership',response={201:GroupMembershipEnvelope,200:GroupMembershipEnvelope})
    def change_group(request,customer_id:UUID,payload:GroupMembership):
        return _mutate(request,f'POST /api/v1/admin/commercial/groups/members/{customer_id}',payload,
            lambda:commercial_admin.change_membership(request.auth,customer_id,payload.group_id,payload.reason))

    @router.get('/commercial/gst-profiles',operation_id='adminCommercialGSTProfilesList',response=GSTListEnvelope)
    def list_gst(request,limit:int=Query(50,ge=1,le=100)):
        rbac.require(request.auth,'invoices.manage')
        from cow.models import CommercialGSTProfile
        return {'data':[commercial_admin.gst_view(p) for p in CommercialGSTProfile.objects.order_by('-created_at')[:limit]]}

    @router.post('/commercial/gst-profiles',operation_id='adminCommercialGSTDraft',response={201:GSTEnvelope,200:GSTEnvelope})
    def create_gst(request,payload:GSTCreate):
        return _mutate(request,'POST /api/v1/admin/commercial/gst-profiles',payload,
            lambda:commercial_admin.create_gst_profile(request.auth,payload.key,payload.version,payload.seller_tax_ref,
                  payload.legal_decision_ref,{k:v.model_dump() for k,v in payload.tax_classes.items()},payload.reason))

    @router.post('/commercial/gst-profiles/{profile_id}/approve',operation_id='adminCommercialGSTApprove',response={200:GSTEnvelope})
    def approve_gst(request,profile_id:UUID,payload:Reason):
        return _mutate(request,f'POST /api/v1/admin/commercial/gst-profiles/{profile_id}/approve',payload,
            lambda:commercial_admin.approve_gst_profile(request.auth,profile_id,payload.reason),status=200)

    @router.post('/commercial/gst-profiles/{profile_id}/duplicate',operation_id='adminCommercialGSTDuplicate',response={201:GSTEnvelope,200:GSTEnvelope})
    def duplicate_gst(request,profile_id:UUID,payload:Duplicate):
        return _mutate(request,f'POST /api/v1/admin/commercial/gst-profiles/{profile_id}/duplicate',payload,
            lambda:commercial_admin.duplicate_gst_profile(request.auth,profile_id,payload.new_key,payload.reason))

    @router.post('/commercial/tariff-assignments',operation_id='adminCommercialTariffAssign',response={201:TargetAssignmentEnvelope,200:TargetAssignmentEnvelope})
    def assign_tariff(request,payload:TariffAssignmentCreate):
        return _mutate(request,'POST /api/v1/admin/commercial/tariff-assignments',payload,
            lambda:commercial_admin.assign_tariff(request.auth,payload.policy_id,payload.target_type,payload.target_id,
                payload.channel,payload.slot,payload.starts_at,payload.ends_at,payload.reason))

    @router.post('/commercial/tariff-assignments/{assignment_id}/replace',operation_id='adminCommercialTariffReplace',response={200:TargetAssignmentEnvelope})
    def replace_tariff(request,assignment_id:UUID,payload:ReplaceTariff):
        return _mutate(request,f'POST /api/v1/admin/commercial/tariff-assignments/{assignment_id}/replace',payload,
            lambda:commercial_admin.replace_tariff(request.auth,assignment_id,payload.new_policy_id,payload.reason),status=200)

    @router.post('/commercial/gst-assignments',operation_id='adminCommercialGSTAssign',response={201:GSTAssignmentEnvelope,200:GSTAssignmentEnvelope})
    def assign_gst(request,payload:GSTAssignmentCreate):
        return _mutate(request,'POST /api/v1/admin/commercial/gst-assignments',payload,
            lambda:commercial_admin.assign_gst(request.auth,payload.profile_id,payload.zone_id,payload.channel,
                payload.starts_at,payload.ends_at,payload.reason))

    @router.post('/commercial/gst-assignments/{assignment_id}/replace',operation_id='adminCommercialGSTReplace',response={200:GSTAssignmentEnvelope})
    def replace_gst(request,assignment_id:UUID,payload:ReplaceGST):
        return _mutate(request,f'POST /api/v1/admin/commercial/gst-assignments/{assignment_id}/replace',payload,
            lambda:commercial_admin.replace_gst(request.auth,assignment_id,payload.new_profile_id,payload.reason),status=200)

    @router.post('/commercial/zones/{zone_id}/channels/{channel}',operation_id='adminCommercialZoneChannelSet',response={200:ChannelEnvelope})
    def set_channel(request,zone_id:UUID,channel:Literal['ROAMING','ON_SITE_QR'],payload:ChannelChange):
        return _mutate(request,f'POST /api/v1/admin/commercial/zones/{zone_id}/channels/{channel}',payload,
            lambda:commercial_admin.set_channel(request.auth,zone_id,channel,payload.enabled,payload.reason),status=200)

    @router.post('/commercial/preview',operation_id='adminCommercialScenarioPreview',response=PreviewEnvelope)
    def preview(request,payload:Preview):
        _json(request)
        return {'data':commercial_admin.preview(request.auth,payload.zone_id,payload.channel,payload.relation,
            payload.at,payload.seller_tax_ref,payload.pricing_group_id,
            {k:v.model_dump() for k,v in payload.measurements.items()},payload.event)}

    @router.post('/commercial/tariffs/{policy_id}/duplicate-and-assign',operation_id='adminCommercialTariffDuplicateAssign',response={201:DupAssignEnvelope,200:DupAssignEnvelope})
    def duplicate_assign(request,policy_id:UUID,payload:DuplicateAssign):
        return _mutate(request,f'POST /api/v1/admin/commercial/tariffs/{policy_id}/duplicate-and-assign',payload,
            lambda:commercial_admin.duplicate_and_assign(request.auth,policy_id,payload.new_key,
                payload.target_type,payload.target_id,payload.channel,payload.reason))

    @router.post('/commercial/tariff-assignments/{assignment_id}/close',operation_id='adminCommercialTariffRemoveTarget',response={200:TargetAssignmentEnvelope})
    def remove_target(request,assignment_id:UUID,payload:Reason):
        return _mutate(request,f'POST /api/v1/admin/commercial/tariff-assignments/{assignment_id}/close',payload,
            lambda:commercial_admin.close_tariff_assignment(request.auth,assignment_id,payload.reason),status=200)

    @router.post('/commercial/gst-assignments/{assignment_id}/close',operation_id='adminCommercialGSTRemoveTarget',response={200:GSTAssignmentEnvelope})
    def remove_gst_target(request,assignment_id:UUID,payload:Reason):
        return _mutate(request,f'POST /api/v1/admin/commercial/gst-assignments/{assignment_id}/close',payload,
            lambda:commercial_admin.close_gst_assignment(request.auth,assignment_id,payload.reason),status=200)

    @router.get('/commercial/zones/{zone_id}/channels/{channel}/readiness',operation_id='adminCommercialZoneReadiness',response=ReadinessEnvelope)
    def readiness(request,zone_id:UUID,channel:Literal['ROAMING','ON_SITE_QR']):
        return {'data':commercial_admin.channel_readiness(request.auth,zone_id,channel)}

    @router.get('/commercial/tariff-assignments',operation_id='adminCommercialTariffAssignmentList',response=TargetAssignmentListEnvelope)
    def tariff_assignments(request,limit:int=Query(50,ge=1,le=100)):
        rbac.require(request.auth,'pricing.read')
        from cow.models import CommercialTariffAssignment
        return {'data':[commercial_admin.tariff_assignment_view(x) for x in
            CommercialTariffAssignment.objects.order_by('-created_at','-id')[:limit]]}

    @router.get('/commercial/gst-assignments',operation_id='adminCommercialGSTAssignmentList',response=GSTAssignmentListEnvelope)
    def gst_assignments(request,limit:int=Query(50,ge=1,le=100)):
        rbac.require(request.auth,'invoices.manage')
        from cow.models import CommercialGSTAssignment
        return {'data':[commercial_admin.gst_assignment_view(x) for x in
            CommercialGSTAssignment.objects.order_by('-created_at','-id')[:limit]]}
