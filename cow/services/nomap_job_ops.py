"""Provider-independent driver/admin job lifecycle and closure gates.

All customer mutations are separate APIs. This module owns Admin assignment,
driver acknowledgement/departure/arrival, verified closure submission and
independent Admin approval. No driver or Admin may directly set settled ledger.
"""
from __future__ import annotations
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from cow.common.errors import reject
from cow.models import (
    CoWBookingCommercialCommitment, CoWCompletionReview, DutySession,
    DutyZoneAssignment, ServiceJob,
)
from cow.services import jobs, booking_commercial as commercial
from cow.services import nomap_location as loc
from cow.services import operational_eligibility as safety
from cow.services import rbac


def _require_gate(name):
    if not getattr(settings,name,False):
        reject(503,'JOB_OPERATION_DISABLED',name+' not authorized')


def _duty(job,actor):
    duty=job.driver_duty
    if (getattr(actor,'plane',None)!='driver' or getattr(actor,'status',None)!='ACTIVE' or
        duty is None or duty.driver.principal_id!=actor.pk or
        duty.ended_at is not None or duty.state!='AVAILABLE'):
        reject(403,'DRIVER_JOB_NOT_AUTHORIZED','Current duty and assignment required')
    return duty


def view(job):
    return {'id':str(job.pk),'state':job.state,'version':job.version,
            'request_id':str(job.request_id),
            'driver_duty_id':str(job.driver_duty_id) if job.driver_duty_id else None,
            'customer_id':str(job.request.customer_id),
            'updated_at':job.updated_at.isoformat()}


def list_driver_jobs(actor,limit=50):
    if getattr(actor,'plane',None)!='driver' or actor.status!='ACTIVE':
        reject(403,'DRIVER_REQUIRED','Active driver required')
    limit=max(1,min(100,int(limit)))
    rows=ServiceJob.objects.select_related('request','driver_duty__driver').filter(
        driver_duty__driver__principal=actor).order_by('-created_at')[:limit]
    return [view(j) for j in rows]


def get_driver_job(actor,job_id):
    job=ServiceJob.objects.select_related('request','driver_duty__driver').filter(pk=job_id).first()
    if job is None:reject(404,'JOB_NOT_FOUND','Job not found')
    _duty(job,actor)
    return view(job)


def admin_assign(*,actor,job_id,duty_id,version,reason):
    _require_gate('COW_DRIVER_WORKFLOW_ENABLED')
    if type(version) is not int or version<1 or not isinstance(reason,str) or not 8<=len(reason)<=500:
        reject(422,'DISPATCH_INPUT_INVALID','Version and 8-500 character reason required')
    with transaction.atomic():
        safety.fresh_admin(actor,'dispatch.manage')
        safety.lock_gate()
        job=ServiceJob.objects.select_for_update().select_related('request').filter(pk=job_id).first()
        if job is None:reject(404,'JOB_NOT_FOUND','Job not found')
        if job.version!=version:reject(412,'JOB_VERSION_CONFLICT','Job changed; reload')
        if job.state!='DISPATCHING' or job.driver_duty_id is not None:
            reject(409,'JOB_NOT_DISPATCHABLE','Job must be unassigned and dispatching')
        booking=CoWBookingCommercialCommitment.objects.select_for_update().filter(job=job,state='FUNDED').first()
        if booking is None:reject(409,'JOB_BOOKING_NOT_FUNDED','Funded commercial booking required')
        duty=DutySession.objects.select_for_update().select_related('driver').filter(pk=duty_id).first()
        if (duty is None or duty.ended_at is not None or duty.state!='AVAILABLE' or
            duty.driver.eligibility!='ACTIVE'):
            reject(409,'DUTY_NOT_ASSIGNABLE','Active eligible available duty required')
        if ServiceJob.objects.filter(driver_duty=duty).exclude(
                state__in=['COMPLETED','CANCELLED','FAILED','EXPIRED']).exists():
            reject(409,'DUTY_ALREADY_HAS_JOB','Driver duty already owns unfinished service')
        zone_id=booking.snapshot.get('context',{}).get('zone_id')
        now=timezone.now()
        if not DutyZoneAssignment.objects.filter(duty=duty,zone_id=zone_id,
                starts_at__lte=now).filter(Q(ends_at__isnull=True)|Q(ends_at__gt=now)).exists():
            reject(409,'DUTY_ZONE_NOT_AUTHORIZED','Duty requires an active assignment to booking Zone')
        job.driver_duty=duty
        job.save(update_fields=['driver_duty','updated_at'])
        jobs.transition_locked(job,'ASSIGNED','ADMIN_DISPATCH_ASSIGNED',
            {'actor_id':str(actor.pk),'duty_id':str(duty.pk),'reason':reason,
             'zone_id':zone_id})
        return view(job)


def driver_transition(*,actor,job_id,version,target):
    _require_gate('COW_DRIVER_WORKFLOW_ENABLED')
    if type(version) is not int or version<1:
        reject(422,'JOB_VERSION_REQUIRED','Positive expected version required')
    transitions={'ACCEPTED':('ASSIGNED','DRIVER_ACCEPTED'),
                 'EN_ROUTE':('ACCEPTED','DRIVER_DEPARTED'),
                 'ARRIVED':('EN_ROUTE','DRIVER_GEOFENCE_ARRIVAL')}
    if target not in transitions:
        reject(422,'JOB_TRANSITION_INVALID','Unsupported driver action')
    with transaction.atomic():
        safety.lock_gate()
        job=ServiceJob.objects.select_for_update().select_related('request','driver_duty__driver').filter(pk=job_id).first()
        if job is None:reject(404,'JOB_NOT_FOUND','Job not found')
        duty=_duty(job,actor)
        if job.version!=version:reject(412,'JOB_VERSION_CONFLICT','Job changed; reload')
        expected,action=transitions[target]
        if job.state!=expected:reject(409,'JOB_STATE_CONFLICT','Unexpected previous job state')
        evidence={'driver_id':str(actor.pk),'duty_id':str(duty.pk)}
        if target=='ARRIVED':
            near=loc.assert_driver_near(job,timezone.now())
            evidence.update({'location_ref':'cow:observation:'+near['observation_id'],
                             'radius_m':near['radius_m'],'measured_m':near['distance_m'],
                             'accuracy_m':near['accuracy_m'],'method':near['method']})
        jobs.transition_locked(job,target,action,evidence)
        return view(job)


def driver_submit_completion(*,actor,job_id,version,driver_ref):
    _require_gate('COW_DRIVER_WORKFLOW_ENABLED')
    commercial._valid_ref(driver_ref)
    with transaction.atomic():
        safety.lock_gate()
        job=ServiceJob.objects.select_for_update().select_related('request','driver_duty__driver').filter(pk=job_id).first()
        if job is None:reject(404,'JOB_NOT_FOUND','Job not found')
        _duty(job,actor)
        if job.version!=version:
            reject(412,'JOB_VERSION_CONFLICT','Job changed; reload')
        if job.state!='CHARGING':
            reject(409,'JOB_NOT_CHARGING','Only completed physical charging may enter wrap-up')
        booking=CoWBookingCommercialCommitment.objects.select_for_update().filter(job=job).first()
        if booking is None or booking.state not in {'IN_SERVICE','START_PENDING'}:
            reject(409,'BOOKING_STATE_INVALID','Commercial booking not ready for completion')
        delivered,refs=commercial._accepted_completed_energy(booking)
        if not refs:
            reject(409,'BOOKING_SERVICE_NOT_PHYSICALLY_PROVEN','No completed physical sessions')
        if delivered<=0:
            reject(409,'BOOKING_ZERO_DELIVERY_REQUIRES_REVIEW',
                   'Zero delivered energy requires an independently approved service-fault policy')
        previous=CoWCompletionReview.objects.select_for_update().filter(booking=booking).first()
        if previous is not None:
            reject(409,'BOOKING_COMPLETION_ALREADY_REVIEWED','Completion review already exists')
        jobs.transition_locked(job,'WRAP_UP','DRIVER_COMPLETION_SUBMITTED',
            {'driver_id':str(actor.pk),'driver_ref':driver_ref,
             'physical_sessions':len(refs),'delivered_wh':delivered})
        review=CoWCompletionReview.objects.create(booking=booking,driver=actor,
                    driver_ref=driver_ref,state='DRIVER_PROPOSED')
        booking.state='CLOSING';booking.save(update_fields=['state','updated_at'])
        return {'job':view(job),'review_id':str(review.pk),
                'state':'DRIVER_PROPOSED','awaits_admin_approval':True}


def admin_approve_completion(*,actor,booking_id,review_ref,reason):
    _require_gate('COW_ADMIN_CLOSURE_APPROVAL_ENABLED')
    commercial._valid_ref(review_ref)
    if not isinstance(reason,str) or not 8<=len(reason)<=500:
        reject(422,'CLOSURE_REASON_INVALID','8-500 character review reason required')
    with transaction.atomic():
        safety.fresh_admin(actor,'jobs.manage')
        rbac.require(actor,'payments.manage')
        safety.lock_gate()
        booking=CoWBookingCommercialCommitment.objects.select_for_update().select_related('job__request').filter(pk=booking_id).first()
        if booking is None:reject(404,'BOOKING_NOT_FOUND','Booking not found')
        job=ServiceJob.objects.select_for_update().filter(pk=booking.job_id).first()
        review=CoWCompletionReview.objects.select_for_update().filter(booking=booking).first()
        if review is None or review.state!='DRIVER_PROPOSED' or job.state!='WRAP_UP':
            reject(409,'BOOKING_CLOSURE_NOT_PENDING','No driver-proposed closure to approve')
        if review.driver_id==actor.pk:
            reject(403,'CLOSURE_SELF_APPROVAL_FORBIDDEN','Independent Admin approval required')
        delivered,refs=commercial._accepted_completed_energy(booking)
        if not refs:
            reject(409,'CLOSURE_PHYSICAL_EVIDENCE_MISSING','Completed HAL session evidence required')
        review.reviewed_by=actor;review.review_ref=review_ref;review.review_reason=reason
        review.state='APPROVED'
        review.save(update_fields=['reviewed_by','review_ref','review_reason','state','updated_at'])
        jobs.transition_locked(job,'COMPLETED','ADMIN_COMPLETION_APPROVED',
            {'review_id':str(review.pk),'review_ref':review_ref,
             'physical_sessions':len(refs),'delivered_wh':delivered})
        return {'job':view(job),'review_id':str(review.pk),
                'state':'APPROVED','completion_ref':review_ref,
                'settlement_not_posted':True}
