"""Admin directories and pricing Group lifecycle, backed by shared services.

Admin is a consumer of Customer/Driver facts, never an owner of their forms.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from ninja import Query, Schema
from pydantic import ConfigDict, Field

from cow.admin.commercial import _mutate
from cow.services import admin_directory, admin_groups


class Strict(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PageCursor(Strict):
    next_cursor: UUID | None


class CustomerItem(Strict):
    customer_id: UUID                    # Principal UUID, not CustomerProfile UUID
    account_id: UUID
    profile_id: UUID | None
    email: str
    email_verified: bool
    status: Literal['ACTIVE', 'PENDING', 'SUSPENDED']
    full_name: str
    created_at: datetime


class CustomerDetail(CustomerItem):
    saved_vehicle_count: int
    saved_address_count: int


class CustomerPage(PageCursor):
    items: list[CustomerItem]


class JobItem(Strict):
    job_id: UUID
    request_id: UUID
    state: str
    version: int
    created_at: datetime


class JobsPage(PageCursor):
    items: list[JobItem]


class DriverItem(Strict):
    driver_id: UUID                     # Driver Principal UUID
    account_id: UUID
    profile_id: UUID | None
    email: str
    email_verified: bool
    status: Literal['ACTIVE', 'PENDING', 'SUSPENDED']
    eligibility: str
    licence_valid_until: date | None
    created_at: datetime


class DriverDetail(DriverItem):
    active_duty_count: int


class DriverPage(PageCursor):
    items: list[DriverItem]


class GroupDetail(Strict):
    id: UUID
    name: str
    description: str
    is_active: bool
    active_member_count: int


class GroupView(Strict):
    id: UUID
    name: str
    description: str
    is_active: bool


class GroupMember(Strict):
    membership_id: UUID
    customer_profile_id: UUID
    customer_id: UUID
    email: str
    full_name: str
    starts_at: datetime
    ends_at: datetime | None


class GroupMemberPage(PageCursor):
    items: list[GroupMember]


class RemoveResult(Strict):
    customer_profile_id: UUID
    removed: bool
    already_absent: bool


class Reason(Strict):
    reason: str = Field(min_length=8, max_length=500)


class CustomerPageEnvelope(Strict):
    data: CustomerPage
class CustomerDetailEnvelope(Strict):
    data: CustomerDetail
class CustomerJobsEnvelope(Strict):
    data: JobsPage
class DriverPageEnvelope(Strict):
    data: DriverPage
class DriverDetailEnvelope(Strict):
    data: DriverDetail
class GroupDetailEnvelope(Strict):
    data: GroupDetail
class GroupMemberPageEnvelope(Strict):
    data: GroupMemberPage
class GroupViewEnvelope(Strict):
    data: GroupView
class RemoveEnvelope(Strict):
    data: RemoveResult


def register(router):
    @router.get('/customers', operation_id='adminCustomersList', response=CustomerPageEnvelope)
    def list_customers(request, limit:int=Query(50,ge=1,le=100), before:UUID|None=None,
                       status:Literal['ACTIVE','PENDING','SUSPENDED']|None=None,
                       email:str|None=Query(None,max_length=254)):
        return {'data':admin_directory.list_customers(request.auth,limit=limit,before=before,status=status,email=email)}

    @router.get('/customers/{customer_id}', operation_id='adminCustomerGet', response=CustomerDetailEnvelope)
    def get_customer(request,customer_id:UUID):
        return {'data':admin_directory.customer_detail(request.auth,customer_id)}

    @router.get('/customers/{customer_id}/jobs', operation_id='adminCustomerJobsList', response=CustomerJobsEnvelope)
    def get_customer_jobs(request,customer_id:UUID,limit:int=Query(50,ge=1,le=100),before:UUID|None=None):
        return {'data':admin_directory.customer_jobs(request.auth,customer_id,limit=limit,before=before)}

    @router.get('/drivers', operation_id='adminDriversList', response=DriverPageEnvelope)
    def list_drivers(request, limit:int=Query(50,ge=1,le=100), before:UUID|None=None,
                     status:Literal['ACTIVE','PENDING','SUSPENDED']|None=None,
                     email:str|None=Query(None,max_length=254)):
        return {'data':admin_directory.list_drivers(request.auth,limit=limit,before=before,status=status,email=email)}

    @router.get('/drivers/{driver_id}', operation_id='adminDriverGet', response=DriverDetailEnvelope)
    def get_driver(request,driver_id:UUID):
        return {'data':admin_directory.driver_detail(request.auth,driver_id)}

    @router.get('/commercial/groups/{group_id}', operation_id='adminCommercialGroupGet', response=GroupDetailEnvelope)
    def get_group(request,group_id:UUID):
        return {'data':admin_groups.group_detail(request.auth,group_id)}

    @router.get('/commercial/groups/{group_id}/members', operation_id='adminCommercialGroupMembers',response=GroupMemberPageEnvelope)
    def members(request,group_id:UUID,limit:int=Query(50,ge=1,le=100),
                before:UUID|None=None,include_history:bool=False):
        return {'data':admin_groups.group_members(request.auth,group_id,limit=limit,before=before,
                                                  include_history=include_history)}

    @router.post('/commercial/groups/members/{customer_profile_id}/remove',
                 operation_id='adminCommercialGroupMemberRemove',response={200:RemoveEnvelope})
    def remove_member(request,customer_profile_id:UUID,payload:Reason):
        return _mutate(request,f'POST /api/v1/admin/commercial/groups/members/{customer_profile_id}/remove',
                       payload,lambda:admin_groups.remove_member(request.auth,customer_profile_id,payload.reason),status=200)

    @router.post('/commercial/groups/{group_id}/deactivate',
                 operation_id='adminCommercialGroupDeactivate',response={200:GroupViewEnvelope})
    def deactivate(request,group_id:UUID,payload:Reason):
        return _mutate(request,f'POST /api/v1/admin/commercial/groups/{group_id}/deactivate',
                       payload,lambda:admin_groups.set_group_active(request.auth,group_id,False,payload.reason),status=200)

    @router.post('/commercial/groups/{group_id}/reactivate',
                 operation_id='adminCommercialGroupReactivate',response={200:GroupViewEnvelope})
    def reactivate(request,group_id:UUID,payload:Reason):
        return _mutate(request,f'POST /api/v1/admin/commercial/groups/{group_id}/reactivate',
                       payload,lambda:admin_groups.set_group_active(request.auth,group_id,True,payload.reason),status=200)
