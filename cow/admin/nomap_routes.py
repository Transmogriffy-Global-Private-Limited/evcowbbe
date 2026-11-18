"""Admin job, financial-closure, wallet-credit refunds and tracking API boundary."""
from uuid import UUID
from django.utils import timezone
from ninja import Schema
from pydantic import ConfigDict, Field
from cow.common.nomap_wire import (
    JobEnvelope, AdminClosureEnvelope, WalletRefundEnvelope, TrackingEnvelope,
)
from cow.common.errors import reject
from cow.models import ServiceJob
from cow.services import idempotency, rbac
from cow.services import nomap_job_ops as ops
from cow.services import nomap_location as location
from cow.services import wallet_refunds as refunds

class Strict(Schema):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class AssignDuty(Strict):
    duty_id: UUID
    expected_version: int=Field(ge=1)
    reason: str=Field(min_length=8,max_length=500)

class Review(Strict):
    review_ref: str=Field(min_length=12,max_length=160)
    reason: str=Field(min_length=8,max_length=500)

class RefundCreate(Strict):
    settlement_id: UUID
    amount_paise: int=Field(gt=0)
    reason: str=Field(min_length=12,max_length=500)
    evidence_ref: str=Field(min_length=12,max_length=160)

class RefundApproval(Strict):
    approval_ref: str=Field(min_length=12,max_length=160)


def _mutation(request,scope,payload,op):
    return idempotency.execute(request.auth,scope,request.headers.get('Idempotency-Key'),
        payload.model_dump(mode='json'),lambda:(200,{'data':op()}),retention_hours=168)[1]


def register(router):
    @router.post('/jobs/{job_id}/assign-duty',response=JobEnvelope,operation_id='cowV3AdminAssignDuty')
    def assign(request,job_id:UUID,payload:AssignDuty):
        return _mutation(request,f'admin:v3:assign:{job_id}',payload,
            lambda:ops.admin_assign(actor=request.auth,job_id=job_id,duty_id=payload.duty_id,
                version=payload.expected_version,reason=payload.reason))

    @router.post('/commercial/bookings/{booking_id}/approve-completion',response=AdminClosureEnvelope,
                 operation_id='cowV3AdminApproveBookingCompletion')
    def approve_completion(request,booking_id:UUID,payload:Review):
        return _mutation(request,f'admin:v3:completion:{booking_id}',payload,
            lambda:ops.admin_approve_completion(actor=request.auth,booking_id=booking_id,
                        review_ref=payload.review_ref,reason=payload.reason))

    @router.post('/commercial/wallet-refunds',response=WalletRefundEnvelope,
                 operation_id='cowV3AdminRequestWalletRefund')
    def request_refund(request,payload:RefundCreate):
        return _mutation(request,f'admin:v3:refund:{payload.settlement_id}',payload,
            lambda:refunds.request_wallet_refund(actor=request.auth,
                settlement_id=payload.settlement_id,amount_paise=payload.amount_paise,
                request_key=request.headers.get('Idempotency-Key',''),
                reason=payload.reason,evidence_ref=payload.evidence_ref))

    @router.post('/commercial/wallet-refunds/{refund_id}/approve',response=WalletRefundEnvelope,
                 operation_id='cowV3AdminApproveWalletRefund')
    def approve_refund(request,refund_id:UUID,payload:RefundApproval):
        return _mutation(request,f'admin:v3:refund:approve:{refund_id}',payload,
            lambda:refunds.approve_and_credit(actor=request.auth,refund_id=refund_id,
                approval_ref=payload.approval_ref))

    @router.get('/jobs/{job_id}/driver-tracking',response=TrackingEnvelope,
                operation_id='cowV3AdminDriverTracking')
    def driver_tracking(request,job_id:UUID):
        rbac.require(request.auth,'dispatch.read')
        job=ServiceJob.objects.select_related('request','driver_duty__driver').filter(pk=job_id).first()
        if job is None:reject(404,'JOB_NOT_FOUND','Job not found')
        # Admin tracking is explicitly scoped to active operational work.
        if job.state not in location.TRACKABLE:
            return {'data':{'job_id':str(job.pk),'tracking_state':'NOT_ACTIVE','latest':None}}
        now=timezone.now()
        row=location.latest_driver(job,now=now)
        state='NO_SIGNAL' if row is None else (
            'LIVE' if location.is_fresh_driver_sample(row,now) else 'STALE')
        return {'data':{'job_id':str(job.pk),'tracking_state':state,
            'latest':location._location_public(row,show_coordinates=True) if row else None}}
