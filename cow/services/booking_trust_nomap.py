"""Server-persisted no-Maps acceptance, OTP and handover trust provider.

Customer address/driver location are device-entered/observed, NOT verified GPS
hardware provenance. Explicit customer consent + driver-entered one-use OTP
are separate checks. No arbitrary client bool `verified` is accepted.
Requires configured HMAC key and source-release gates to perform operations.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import UUID
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from cow.common.errors import reject
from cow.models import (
    ServiceRequest, ServiceJob, CoWBookingCommercialCommitment,
    CoWBookingPreviewTicket, CoWHandoverTicket, CoWCompletionReview,
    ChargerConnector,
)
from cow.services import booking_commercial as commercial
from cow.services import nomap_location as loc
from cow.services import operational_eligibility as safety
from cow.services.charging_commercial_policy import stable_digest


def _sha(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _handover_secret():
    secret=getattr(settings,'COW_HANDOVER_HMAC_KEY','')
    if not isinstance(secret,str) or len(secret)<32:
        reject(503,'HANDOVER_KEY_UNCONFIGURED','OTP HMAC key must be configured')
    return secret.encode('utf-8')


def _otp_digest(ticket_id,otp):
    return hmac.new(_handover_secret(),f'{ticket_id}:{otp}'.encode('ascii'),
                    hashlib.sha256).hexdigest()


def _consent_token(ticket):
    # Fresh, random UUID ticket identity and immutable OTP/duty/connector binding
    # produce a stable high-entropy bearer token for loss-safe HTTP retries.
    # Do not persist or log this token; only its SHA-256 is stored.
    if ticket.connector_id is None or not ticket.otp_digest:
        reject(409,'HANDOVER_CONSENT_CONTEXT_INVALID','Verified connector/OTP required')
    message=':'.join((
        'cow-consent-v2',str(ticket.pk),str(ticket.booking_id),str(ticket.owner_id),
        str(ticket.driver_id),str(ticket.duty_id),str(ticket.connector_id),
        ticket.otp_digest,
    )).encode('ascii')
    return hmac.new(_handover_secret(),message,hashlib.sha256).hexdigest()


def _otp_enabled():
    if not getattr(settings,'COW_HANDOVER_OTP_ENABLED',False):
        reject(503,'HANDOVER_OTP_DISABLED','Handover OTP workflow disabled')


def _requires_mode():
    if getattr(settings,'COW_LOCATION_MODE','DISABLED')!='DEVICE_GPS_WITH_OTP':
        reject(503,'BOOKING_LOCATION_MODE_DISABLED','No-Maps booking trust mode not authorized')


def _time(v):
    if type(v) is not str:
        reject(409,'TRUST_SNAPSHOT_TIME_INVALID','Invalid frozen time')
    try:
        from datetime import datetime
        x=datetime.fromisoformat(v.replace('Z','+00:00'))
        if x.tzinfo is None:raise ValueError('naive')
        return x
    except (ValueError,TypeError) as exc:
        reject(409,'TRUST_SNAPSHOT_TIME_INVALID','Invalid frozen time')


def _ensure_customer(actor):
    if getattr(actor,'plane',None)!='app' or getattr(actor,'status',None)!='ACTIVE':
        reject(403,'TRUST_CUSTOMER_INVALID','Active customer required')


def _booking_for_customer(actor,booking_id,*,lock=False):
    qs=CoWBookingCommercialCommitment.objects
    if lock:qs=qs.select_for_update()
    b=qs.select_related('job__request','job__driver_duty__driver','owner').filter(
        pk=booking_id,owner=actor).first()
    if b is None:reject(404,'BOOKING_NOT_FOUND','Booking not found')
    return b


def _driver_on_job(actor,job):
    d=job.driver_duty
    if (getattr(actor,'plane',None)!='driver' or getattr(actor,'status',None)!='ACTIVE' or
        d is None or d.driver.principal_id!=actor.pk or d.ended_at is not None or
        d.state!='AVAILABLE'):
        reject(403,'HANDOVER_DRIVER_UNAUTHORIZED','Active assigned duty required')
    return d


def preview_acceptance(*,owner,request_id,zone_id):
    """Create one-use customer acceptance capability for exact quoted terms.

    Distance policy: published Zone center -> frozen customer address WGS84
    GEODESIC, not driven road distance. It is an explicit product policy mode.
    """
    _requires_mode();_ensure_customer(owner)
    if not getattr(settings,'COW_BOOKING_COMMERCIAL_ACCEPT_ENABLED',False):
        reject(503,'BOOKING_ACCEPTANCE_DISABLED','Booking acceptance disabled')
    with transaction.atomic():
        safety.lock_gate()
        req=ServiceRequest.objects.select_for_update().filter(pk=request_id,customer=owner).first()
        if req is None:reject(404,'REQUEST_NOT_FOUND','Service request not found')
        if ServiceJob.objects.filter(request=req).exists():
            reject(409,'BOOKING_ALREADY_ACCEPTED','Request already committed')
        if not isinstance(req.address_snapshot,dict):
            reject(409,'BOOKING_ADDRESS_INVALID','Frozen address unavailable')
        try:
            lat=req.address_snapshot['latitude'];lon=req.address_snapshot['longitude']
        except KeyError:
            reject(409,'BOOKING_ADDRESS_INVALID','Missing coordinates in frozen address')
        zone,relation,distance=loc.published_zone_radius(lat,lon,zone_id)
        now=timezone.now()
        # The DB ticket ID is generated by server. Human-readable refs are immutable.
        from uuid import uuid4
        ref_id=uuid4()
        proof=commercial.BookingAcceptanceProof(
            request_id=str(req.pk),zone_id=str(zone.pk),relation=relation,
            approved_distance_m=distance,
            buffer_paise=settings.COW_BOOKING_PROTECTED_BUFFER_PAISE,
            total_energy_wh_ceiling=req.requested_energy_wh,
            per_session_wh_ceiling=min(req.requested_energy_wh,settings.COW_BOOKING_SESSION_CEILING_WH),
            verified_at=now,
            location_ref=f'cow:address:{req.pk}',route_ref=f'cow:zone-geodesic:{ref_id}',
            serviceability_ref=f'cow:published-zone:{zone.pk}',
            acceptance_ref=f'cow:acceptance:{ref_id}')
        proof.check(now)
        summary,_snapshot=commercial._booking_disclosure(owner,req,proof,now)
        digest=stable_digest({'summary':summary,'acceptance_ref':proof.acceptance_ref})
        token=secrets.token_urlsafe(32)
        frozen={'request_id':proof.request_id,'zone_id':proof.zone_id,
          'relation':proof.relation,'approved_distance_m':distance,
          'buffer_paise':proof.buffer_paise,
          'total_energy_wh_ceiling':proof.total_energy_wh_ceiling,
          'per_session_wh_ceiling':proof.per_session_wh_ceiling,
          'verified_at':now.isoformat(),
          'location_ref':proof.location_ref,'route_ref':proof.route_ref,
          'serviceability_ref':proof.serviceability_ref,
          'acceptance_ref':proof.acceptance_ref}
        CoWBookingPreviewTicket.objects.create(
            id=ref_id,owner=owner,request=req,zone=zone,token_sha256=_sha(token),
            proof_snapshot=frozen,summary_sha256=stable_digest(summary),
            acceptance_digest=digest,expires_at=now+timedelta(minutes=4))
        return {'request_id':str(req.pk),'zone_id':str(zone.pk),
            'acceptance_token':token,'expires_at':(now+timedelta(minutes=4)).isoformat(),
            'summary':summary,'summary_sha256':stable_digest(summary),
            'distance_method':'ZONE_CENTER_TO_ADDRESS_GREAT_CIRCLE',
            'distance_not_driving_route':True,
            'requires_explicit_customer_acceptance':True}


def issue_otp(*,owner,booking_id,issue_key):
    _requires_mode();_ensure_customer(owner)
    commercial._require_key(issue_key)
    _otp_enabled()
    with transaction.atomic():
        safety.lock_gate()
        b=_booking_for_customer(owner,booking_id,lock=True)
        job=ServiceJob.objects.select_for_update().select_related('driver_duty__driver').get(pk=b.job_id)
        if job.state not in {'ARRIVED','SERVICE_PREPARING','CHARGING'} or b.state not in {
            'FUNDED','IN_SERVICE','START_PENDING'}:
            reject(409,'HANDOVER_JOB_NOT_READY','Booking not ready for handover')
        duty=job.driver_duty
        if duty is None or duty.ended_at is not None or duty.state!='AVAILABLE':
            reject(409,'HANDOVER_DUTY_INVALID','Live assigned duty required')
        # No OTP may authorize a second start while previous physical result is uncertain.
        if job.state=='CHARGING': commercial._accepted_completed_energy(b)
        near=loc.assert_driver_near(job,timezone.now())
        prior=CoWHandoverTicket.objects.filter(booking=b,issue_key=issue_key).first()
        if prior is not None:
            return {'ticket_id':str(prior.pk),'state':prior.state,
                    'otp':None,'replayed':True,'expires_at':prior.expires_at.isoformat()}
        active=list(CoWHandoverTicket.objects.select_for_update().filter(
            booking=b,state__in=['ISSUED','DRIVER_VERIFIED','CONSENT_READY']))
        if any(row.state in {'DRIVER_VERIFIED','CONSENT_READY'} and row.expires_at>timezone.now()
               for row in active):
            reject(409,'HANDOVER_ALREADY_VERIFIED','Complete or expire the existing verified handover')
        for row in active:
            row.state='EXPIRED';row.save(update_fields=['state','updated_at'])
        # Bound OTP reissuance; recovery uses a NEW request key, previous OTP invalidated.
        if CoWHandoverTicket.objects.filter(booking=b,created_at__gte=timezone.now()-timedelta(hours=1)).count()>=5:
            reject(429,'HANDOVER_OTP_RATE_LIMIT','Too many recent handover OTP issuances')
        otp=f'{secrets.randbelow(1_000_000):06d}'
        from uuid import uuid4
        tid=uuid4()
        t=CoWHandoverTicket.objects.create(
            id=tid,booking=b,owner=owner,driver=duty.driver.principal,
            duty=duty,issue_key=issue_key,
            otp_digest=_otp_digest(tid,otp),
            expires_at=timezone.now()+timedelta(minutes=5),
            arrival_ref='cow:observation:'+near['observation_id'])
        return {'ticket_id':str(t.pk),'otp':otp,'state':'ISSUED','replayed':False,
                'expires_at':t.expires_at.isoformat(),
                'note':'Show this one-use OTP to the assigned driver; never send it to Admin or logs'}


def driver_verify_otp(*,actor,ticket_id,otp,connector_id):
    _requires_mode();_otp_enabled()
    if not isinstance(otp,str) or len(otp)!=6 or not otp.isascii() or not otp.isdigit():
        reject(422,'HANDOVER_OTP_FORMAT_INVALID','Six-digit OTP required')
    with transaction.atomic():
        safety.lock_gate()
        t=CoWHandoverTicket.objects.select_related('booking__job__driver_duty__driver').select_for_update().filter(pk=ticket_id).first()
        if t is None:reject(404,'HANDOVER_NOT_FOUND','Handover unavailable')
        job=ServiceJob.objects.select_for_update().get(pk=t.booking.job_id)
        duty=_driver_on_job(actor,job)
        if t.driver_id!=actor.pk or t.duty_id!=duty.pk:
            reject(403,'HANDOVER_ACTOR_CHANGED','Handover was issued for different driver/duty')
        if t.state!='ISSUED' or timezone.now()>t.expires_at:
            if t.state=='ISSUED' and timezone.now()>t.expires_at:
                t.state='EXPIRED';t.save(update_fields=['state','updated_at'])
            return {'ticket_id':str(t.pk),'verified':False,'state':t.state,'remaining_attempts':0}
        if t.attempts>=5:
            t.state='EXPIRED';t.save(update_fields=['state','updated_at'])
            return {'ticket_id':str(t.pk),'verified':False,'state':'EXPIRED','remaining_attempts':0}
        t.attempts+=1
        if not hmac.compare_digest(t.otp_digest,_otp_digest(t.pk,otp)):
            if t.attempts>=5:t.state='EXPIRED'
            t.save(update_fields=['attempts','state','updated_at'])
            return {'ticket_id':str(t.pk),'verified':False,'state':t.state,
                    'remaining_attempts':max(0,5-t.attempts)}
        connector=ChargerConnector.objects.select_for_update().filter(
            pk=connector_id,charger_id=duty.charger_id,active=True).first()
        if connector is None:
            reject(409,'HANDOVER_CONNECTOR_INVALID','Connector not associated with assigned charger')
        near=loc.assert_driver_near(job,timezone.now())
        t.connector=connector;t.state='DRIVER_VERIFIED'
        t.verified_at=timezone.now();t.arrival_ref='cow:observation:'+near['observation_id']
        t.save(update_fields=['connector','attempts','state','verified_at','arrival_ref','updated_at'])
        if job.state=='ARRIVED':
            from cow.services import jobs
            jobs.transition_locked(job,'SERVICE_PREPARING','HANDOVER_OTP_VERIFIED',
                {'ticket_id':str(t.pk),'driver_id':str(actor.pk),
                 'location_ref':t.arrival_ref})
        return {'ticket_id':str(t.pk),'verified':True,'state':'DRIVER_VERIFIED',
                'remaining_attempts':5-t.attempts}


def customer_consent(*,owner,ticket_id):
    """One logical consent issuance per ticket, safe across lost HTTP replies.

    The per-ticket row lock serializes first issuance and all retries. A
    previously consumed, expired, legacy-random, or HMAC-rotated token is
    never reissued or invalidated to manufacture a successful retry.
    """
    _requires_mode();_ensure_customer(owner);_otp_enabled()
    with transaction.atomic():
        safety.lock_gate()
        t=CoWHandoverTicket.objects.select_for_update().select_related(
            'booking__job__driver_duty__driver','duty__driver','connector').filter(
            pk=ticket_id,owner=owner).first()
        if t is None:reject(404,'HANDOVER_NOT_FOUND','Handover unavailable')
        now=timezone.now()
        if t.state not in {'DRIVER_VERIFIED','CONSENT_READY'} or now>t.expires_at:
            reject(409,'HANDOVER_NOT_VERIFIED','Driver OTP must be verified and still valid')
        job=t.booking.job
        duty=job.driver_duty
        if (job.state not in {'SERVICE_PREPARING','CHARGING'} or
            t.booking.owner_id!=owner.pk or duty is None or
            t.duty_id!=duty.pk or t.driver_id!=duty.driver.principal_id or
            duty.ended_at is not None or duty.state!='AVAILABLE' or
            duty.driver.eligibility!='ACTIVE' or t.driver.status!='ACTIVE' or
            t.connector_id is None or t.connector.charger_id!=duty.charger_id or
            not t.connector.active):
            reject(409,'HANDOVER_JOB_OR_DUTY_CHANGED',
                   'Booking, driver, duty or connector no longer eligible')
        # Fresh presence is required on both first issuance and credential
        # replay. This is device GPS, not cryptographic hardware attestation.
        near=loc.assert_driver_near(job,now)
        token=_consent_token(t)
        if t.state=='CONSENT_READY':
            if not hmac.compare_digest(t.consent_token_sha256,_sha(token)):
                reject(409,'HANDOVER_CONSENT_NOT_REPLAYABLE',
                       'Earlier consent credential cannot be recovered; use it or reverify after expiry')
            replayed=True
            # Critically: do not rotate token, mutate proof refs, refresh TTL,
            # update verified_at or re-consume driver OTP.
        else:
            t.consent_token_sha256=_sha(token)
            t.consent_ref='cow:consent:'+str(t.pk)
            t.arrival_ref='cow:observation:'+near['observation_id']
            t.verified_at=now
            t.state='CONSENT_READY'
            t.save(update_fields=[
                'consent_token_sha256','consent_ref','arrival_ref',
                'verified_at','state','updated_at'])
            replayed=False
        return {'booking_id':str(t.booking_id),'ticket_id':str(t.pk),
                'handover_token':token,'expires_at':t.expires_at.isoformat(),
                'state':t.state,'proof':'CUSTOMER_CONSENT_AFTER_DRIVER_OTP',
                'replayed':replayed}


class NoMapBookingProvider:
    """Configured as cow.services.booking_trust_nomap.provider."""
    def resolve_acceptance(self,owner,request_id,token,now):
        _requires_mode();_ensure_customer(owner)
        if not isinstance(token,str) or len(token)>250:return None
        row=CoWBookingPreviewTicket.objects.filter(
            owner=owner,request_id=request_id,token_sha256=_sha(token)).first()
        if row is None or row.expires_at<now:
            reject(409,'BOOKING_PREVIEW_EXPIRED','Approved preview token missing or expired')
        proof=row.proof_snapshot
        return commercial.BookingAcceptanceProof(**{**proof,'verified_at':_time(proof['verified_at'])})

    def verify_acceptance(self,owner,proof,summary,digest,now):
        row=CoWBookingPreviewTicket.objects.select_for_update().filter(
            owner=owner,request_id=proof.request_id,
            token_sha256__isnull=False,proof_snapshot__acceptance_ref=proof.acceptance_ref).first()
        if row is None or row.state!='ISSUED' or row.expires_at<now:
            return False
        if (row.summary_sha256!=stable_digest(summary) or row.acceptance_digest!=digest or
            row.proof_snapshot['zone_id']!=proof.zone_id or
            row.proof_snapshot['approved_distance_m']!=proof.approved_distance_m):
            return False
        row.state='CONSUMED';row.consumed_at=now
        row.save(update_fields=['state','consumed_at','updated_at'])
        return True

    def resolve_handover(self,owner,booking_id,token,now):
        _requires_mode();_ensure_customer(owner);_otp_enabled()
        if not isinstance(token,str) or len(token)>250:
            reject(409,'HANDOVER_TOKEN_INVALID','Handover token missing')
        t=CoWHandoverTicket.objects.select_related(
            'booking__job__driver_duty__driver','duty','connector').filter(
            owner=owner,booking_id=booking_id,consent_token_sha256=_sha(token)).first()
        if t is None or t.expires_at<now or t.state not in {'CONSENT_READY','CONSUMED'} or t.connector_id is None:
            reject(409,'HANDOVER_TOKEN_EXPIRED','Handover token invalid/expired')
        return commercial.StartHandoverProof(
            job_id=str(t.booking.job_id),owner_id=str(owner.pk),
            charger_id=str(t.duty.charger_id),connector_id=str(t.connector_id),
            duty_id=str(t.duty_id),verified_at=t.verified_at,
            arrival_location_ref=t.arrival_ref,duty_ref='cow:duty:'+str(t.duty_id),
            otp_consumed_ref='cow:otp:'+str(t.pk),customer_consent_ref=t.consent_ref)

    def verify_handover(self,owner,booking,proof,now):
        _requires_mode();_otp_enabled()
        tid=proof.otp_consumed_ref.removeprefix('cow:otp:')
        t=CoWHandoverTicket.objects.select_for_update().select_related(
            'booking__job__driver_duty__driver').filter(pk=tid,owner=owner,booking=booking).first()
        if (t is None or t.state!='CONSENT_READY' or t.expires_at<now or
            str(t.duty_id)!=proof.duty_id or str(t.connector_id)!=proof.connector_id or
            t.arrival_ref!=proof.arrival_location_ref or
            t.consent_ref!=proof.customer_consent_ref or t.verified_at!=proof.verified_at):
            return False
        loc.assert_driver_near(t.booking.job,now)
        t.state='CONSUMED'
        t.save(update_fields=['state','updated_at'])
        return True

    def verify_driver_completion(self,booking,job,final):
        """Requires an independent Admin-approved closure; driver alone cannot earn minimum."""
        review=CoWCompletionReview.objects.select_for_update().filter(
            booking=booking,state='APPROVED').first()
        if review is None or review.reviewed_by_id==review.driver_id:
            return False
        if final.get('completion_ref')!=review.review_ref:
            return False
        if job.state!='COMPLETED' or final.get('booking_id')!=str(booking.pk):
            return False
        if final.get('total_gross_paise',0)<0 or final.get('not_a_tax_invoice') is not True:
            return False
        return True


provider=NoMapBookingProvider()
