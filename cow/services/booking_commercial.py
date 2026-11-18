"""CoW's one-booking / many-HAL-sessions commercial authority (source only).

The existing cow-commercial-v1 engine is the sole tariff/GST evaluator.
This service never calls Go or the charger and never infers OTP/GPS truth.
Every financial/physical decision requires a trusted provider and a locked
booking+wallet snapshot. Financial posting is a separate disabled gate.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as utc_zone
from decimal import Decimal
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from cow.common.errors import reject
from cow.domain.booking_charge_budget import (
    BookingBudgetError, booking_affordability, determine_cap,
    minimum_for_distance, money, safe_total, validate_minimum_schedule,
)
from cow.domain.commercial_machine import (
    Assignment, GSTAssignment, GSTProfile, Context, Measurement,
    CommercialError, evaluate, split_tax, exact, HEADS,
)
from cow.hal import v1_machine, v1_charging
from cow.models import (
    AuditEvent, Charger, ChargerConnector, ChargingSession, HALCommand,
    HALV1ChargingCommand, HALV1ChargingIntent, HALV1ChargingPermit,
    HALV1ChargingSession, HALV1ChargerOperation, HALV1Mapping, HALV1FactReceipt,
    WalletAccount, WalletTransaction, ServiceRequest, ServiceJob, Quote,
    ServiceZone, CommercialZoneChannel, CustomerProfile,
    CommercialGroupMembership, CommercialTariffAssignment,
    CommercialGSTAssignment, PricingPolicy, CoWMinimumChargingSchedule,
    CoWBookingCommercialCommitment, CoWBookingChargingAllocation,
    CoWBookingFinancialSettlement,
)
from cow.services import operational_eligibility as safety
from cow.services import wallet_authority as wallet_ledger
from cow.services import jobs as job_lifecycle
from cow.services.charging_commercial_policy import (
    stable_digest, aware_utc, canonical_id, ChargingCommercialError,
)

ACTIVE_BOOKING_STATES = frozenset({
    'FUNDED','START_PENDING','IN_SERVICE','CLOSING','RECONCILIATION_REQUIRED',
})
MAX_BOOKING_DISTANCE_M = 1_000_000
MAX_PROOF_AGE = timedelta(minutes=5)


def fail(code, message='Commercial charging cannot safely proceed'):
    reject(409, code, message)


def _at(instant):
    return aware_utc(instant).isoformat().replace('+00:00', 'Z') if instant is not None else None


def _datetime(raw):
    if raw is None:
        return None
    if type(raw) is not str:
        raise BookingBudgetError('FROZEN_TIME_INVALID')
    try:
        return aware_utc(datetime.fromisoformat(raw.replace('Z', '+00:00')))
    except (TypeError, ValueError) as exc:
        raise BookingBudgetError('FROZEN_TIME_INVALID') from exc


def _window(qs, instant):
    return qs.filter(Q(starts_at__isnull=True)|Q(starts_at__lte=instant)).filter(
        Q(ends_at__isnull=True)|Q(ends_at__gt=instant))


def _require_key(key):
    if type(key) is not str or not 16 <= len(key) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in key):
        fail('BOOKING_IDEMPOTENCY_KEY_INVALID', 'Safe 16-128 character idempotency key required')


def _verify_fresh(when, now):
    when = aware_utc(when)
    now = aware_utc(now)
    if when > now or now - when > MAX_PROOF_AGE:
        fail('BOOKING_PROOF_STALE', 'Fresh independently verified evidence required')


def _valid_ref(s):
    if type(s) is not str or not 12 <= len(s) <= 200 or any(ord(c) < 32 or ord(c) == 127 for c in s):
        fail('BOOKING_PROOF_INVALID', 'Durable trusted evidence reference required')


@dataclass(frozen=True)
class BookingAcceptanceProof:
    request_id: str
    zone_id: str
    relation: str
    approved_distance_m: int
    buffer_paise: int
    total_energy_wh_ceiling: int
    per_session_wh_ceiling: int
    verified_at: datetime
    location_ref: str
    route_ref: str
    serviceability_ref: str
    acceptance_ref: str

    def check(self, now):
        for value in (self.request_id, self.zone_id): canonical_id(value)
        if self.relation not in {'INSIDE', 'OUTSIDE'}:
            fail('BOOKING_RELATION_INVALID')
        if type(self.approved_distance_m) is not int or not 0 <= self.approved_distance_m <= MAX_BOOKING_DISTANCE_M:
            fail('BOOKING_DISTANCE_NOT_VERIFIED')
        money(self.buffer_paise, 'buffer')
        if self.buffer_paise <= 0:
            fail('BOOKING_BUFFER_NOT_APPROVED','Approved positive safety buffer required')
        for value in (self.total_energy_wh_ceiling, self.per_session_wh_ceiling):
            if type(value) is not int or value <= 0 or value >= 2**63:
                fail('BOOKING_ENERGY_BOUND_INVALID')
        if self.per_session_wh_ceiling > self.total_energy_wh_ceiling:
            fail('BOOKING_ENERGY_BOUND_INVALID')
        for value in (self.location_ref, self.route_ref, self.serviceability_ref, self.acceptance_ref):
            _valid_ref(value)
        _verify_fresh(self.verified_at, now)


@dataclass(frozen=True)
class StartHandoverProof:
    job_id: str
    owner_id: str
    charger_id: str
    connector_id: str
    duty_id: str
    verified_at: datetime
    arrival_location_ref: str
    duty_ref: str
    otp_consumed_ref: str
    customer_consent_ref: str

    def check(self, now):
        for value in (self.job_id,self.owner_id,self.charger_id,self.connector_id,self.duty_id):
            canonical_id(value)
        for value in (self.arrival_location_ref,self.duty_ref,
                      self.otp_consumed_ref,self.customer_consent_ref):
            _valid_ref(value)
        _verify_fresh(self.verified_at, now)


def _assert_customer(owner):
    if getattr(owner,'plane',None) != 'app' or getattr(owner,'status',None) != 'ACTIVE':
        fail('BOOKING_CUSTOMER_NOT_ACTIVE')


def _group_for(owner, now):
    customer = CustomerProfile.objects.filter(principal=owner).first()
    if customer is None:
        fail('BOOKING_CUSTOMER_PROFILE_REQUIRED')
    members = list(_window(CommercialGroupMembership.objects.select_related('group').filter(
        customer=customer),now)[:2])
    if len(members)>1:
        fail('BOOKING_GROUP_AMBIGUOUS')
    if not members:
        return None
    if not members[0].group.is_active:
        fail('BOOKING_GROUP_INACTIVE')
    return str(members[0].group_id)


def _catalog(zone, channel, group_id, now):
    scope = Q(zone=zone)
    if group_id is not None:
        scope |= Q(group_id=UUID(group_id))
    rows = list(CommercialTariffAssignment.objects.filter(scope,channel=channel)
                .select_related('policy__commercial_payload'))
    assignments, tariffs = [], {}
    for row in rows:
        if row.policy.status != 'PUBLISHED' or not hasattr(row.policy,'commercial_payload'):
            continue
        payload = row.policy.commercial_payload
        key = (row.policy.policy_key,row.policy.policy_version)
        if payload.channel != channel or (payload.plan.get('key'),payload.plan.get('version')) != key:
            fail('BOOKING_TARIFF_PUBLICATION_MISMATCH')
        if key in tariffs and tariffs[key] != payload.plan:
            fail('BOOKING_TARIFF_REVISION_CONFLICT')
        tariffs[key] = deepcopy(payload.plan)
        assignments.append(Assignment(str(row.pk),key[0],key[1],row.target_type,
            str(row.zone_id if row.target_type=='ZONE' else row.group_id),
            row.channel,row.slot,row.starts_at,row.ends_at))
    if not assignments:
        fail('BOOKING_TARIFF_UNAVAILABLE')
    rows = list(CommercialGSTAssignment.objects.filter(zone=zone,channel=channel)
                .select_related('profile'))
    gst_assignments, gst_profiles = [], {}
    active = []
    for row in rows:
        p = row.profile
        key = (str(p.pk),p.profile_version)
        gst_profiles[key] = GSTProfile(str(p.pk),p.profile_version,p.seller_tax_ref,
                                      p.legal_decision_ref,p.status,deepcopy(p.tax_classes))
        gst_assignments.append(GSTAssignment(str(row.pk),str(row.zone_id),row.channel,
                                             str(p.pk),p.profile_version,row.starts_at,row.ends_at))
        if (row.starts_at is None or row.starts_at <= now) and (row.ends_at is None or now < row.ends_at):
            active.append(row)
    if len(active) != 1 or active[0].profile.status != 'APPROVED':
        fail('BOOKING_GST_AUTHORITY_UNAVAILABLE')
    seller = active[0].profile.seller_tax_ref
    if not seller or not active[0].profile.legal_decision_ref:
        fail('BOOKING_GST_LEGAL_EVIDENCE_REQUIRED')
    snapshot = {
        'assignments':[{'id':a.id,'tariff_key':a.tariff_key,'tariff_version':a.tariff_version,
          'target_type':a.target_type,'target_id':a.target_id,'channel':a.channel,
          'slot':a.slot,'starts_at':_at(a.starts_at),'ends_at':_at(a.ends_at)} for a in assignments],
        'tariffs':[{'key':k[0],'version':k[1],'plan':deepcopy(p)} for k,p in sorted(tariffs.items())],
        'gst_assignments':[{'id':a.id,'zone_id':a.zone_id,'channel':a.channel,
           'profile_id':a.profile_id,'profile_version':a.profile_version,
           'starts_at':_at(a.starts_at),'ends_at':_at(a.ends_at)} for a in gst_assignments],
        'gst_profiles':[{'id':p.id,'version':p.version,'seller_ref':p.seller_ref,
          'legal_decision_ref':p.legal_decision_ref,'status':p.status,
          'tax_classes':deepcopy(p.tax_classes)} for p in gst_profiles.values()],
    }
    return assignments,tariffs,gst_assignments,gst_profiles,seller,snapshot


def _rehydrate(snapshot):
    try:
        assignments=[Assignment(x['id'],x['tariff_key'],x['tariff_version'],x['target_type'],
           x['target_id'],x['channel'],x['slot'],_datetime(x['starts_at']),_datetime(x['ends_at']))
           for x in snapshot['assignments']]
        tariffs={(x['key'],x['version']):deepcopy(x['plan']) for x in snapshot['tariffs']}
        gst_assignments=[GSTAssignment(x['id'],x['zone_id'],x['channel'],x['profile_id'],
          x['profile_version'],_datetime(x['starts_at']),_datetime(x['ends_at']))
          for x in snapshot['gst_assignments']]
        gst_profiles={(x['id'],x['version']):GSTProfile(x['id'],x['version'],x['seller_ref'],
            x['legal_decision_ref'],x['status'],deepcopy(x['tax_classes']))
            for x in snapshot['gst_profiles']}
    except (KeyError,TypeError,ValueError,CommercialError,BookingBudgetError) as exc:
        raise BookingBudgetError('BOOKING_CATALOG_SNAPSHOT_INVALID') from exc
    return assignments,tariffs,gst_assignments,gst_profiles


def _ctx(snapshot):
    s = snapshot['context']
    return Context(zone_id=s['zone_id'],channel=s['channel'],relation=s['relation'],
        at=_datetime(s['decision_at']),seller_ref=s['seller_ref'],
        pricing_group_id=s['group_id'],serviceable=True,operational=True,verified=True)


def _measurements(distance_m, energy_wh=0, *, estimated=True):
    return {
        'energy_wh':Measurement(energy_wh,'FROZEN_ENERGY_BOUND' if estimated else 'AUTHENTICATED_HAL_FINAL',estimated),
        'charging_seconds':Measurement(0,'CHARGING_TIME_NOT_BILLABLE',False),
        'session_completed':Measurement(1,'SESSION_COMPLETION_HYPOTHESIS',estimated),
        'travel_m':Measurement(distance_m,'VERIFIED_BOOKING_ROUTE',False),
        'waiting_seconds':Measurement(0,'NO_WAITING_CHARGE',False),
    }


def _eval(context,catalog,event,measurements,mode='PREVIEW'):
    a,t,ga,gp = _rehydrate(catalog)
    return evaluate(context=context,event=event,assignments=a,tariffs=t,
                    gst_assignments=ga,gst_profiles=gp,
                    measurements=measurements,mode=mode)


def _only_charging(catalog):
    """Use the SAME canonical engine with selected tariff charging lines only.

    Other fare components are separately frozen and must not change the HAL cap.
    Removing other components also avoids charging-side GST rounding being
    affected by unrelated upfront service fees.
    """
    isolated = deepcopy(catalog)
    for row in isolated['tariffs']:
        for relation, profile in row['plan']['profiles'].items():
            row['plan']['profiles'][relation] = {'charging': deepcopy(profile['charging'])}
    return isolated


def _verify_snap(booking):
    if (booking.currency != 'INR' or booking.snapshot_sha256 != stable_digest(booking.snapshot) or
        booking.snapshot.get('job_id') != str(booking.job_id) or
        booking.snapshot.get('owner_id') != str(booking.owner_id) or
        booking.snapshot.get('fixed_fees_paise') != booking.fixed_fees_paise or
        booking.snapshot.get('minimum_charging_paise') != booking.minimum_charging_paise or
        booking.snapshot.get('buffer_paise') != booking.protected_buffer_paise):
        fail('BOOKING_FROZEN_AUTHORITY_CHANGED')


def _audit(owner, booking, action, **details):
    AuditEvent.objects.create(actor=owner,target_type='CoWBookingCommercial',
        target_id=booking.pk,action=action,details=details)


def _active_holds_locked(wallet, *, exclude_booking_id=None):
    """Single wallet-serialized available funds across CoW commercial jobs."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Wallet row must be locked in atomic transaction')
    held = wallet_ledger.holds_locked(wallet)
    if exclude_booking_id is not None:
        own = CoWBookingCommercialCommitment.objects.filter(
            wallet=wallet,pk=exclude_booking_id,state__in=ACTIVE_BOOKING_STATES
        ).values_list('reserved_paise',flat=True).first() or 0
        held -= own
    return held


def _wallet_locked(owner):
    wallet, _ = WalletAccount.objects.select_for_update().get_or_create(
        customer=owner,defaults={'currency':'INR','balance_paise':0,'version':1})
    if wallet.currency != 'INR' or type(wallet.balance_paise) is not int or wallet.balance_paise < 0:
        fail('BOOKING_WALLET_CURRENCY_OR_BALANCE_INVALID')
    return wallet


def _policy_and_minimum(zone, context, catalog, approved_distance_m):
    try:
        choice = _eval(context,catalog,'COMPLETED',_measurements(approved_distance_m,estimated=True))
    except (CommercialError,BookingBudgetError,ChargingCommercialError) as exc:
        fail(getattr(exc,'code','BOOKING_PRICING_INVALID'))
    key = choice['selection']['tariff_key']
    version = choice['selection']['tariff_version']
    revision = PricingPolicy.objects.select_for_update().filter(policy_key=key,
        policy_version=version,status='PUBLISHED').first()
    if revision is None:
        fail('BOOKING_PUBLISHED_TARIFF_NOT_FOUND')
    schedule = CoWMinimumChargingSchedule.objects.select_for_update().filter(policy=revision).first()
    if schedule is None or not schedule.authorization_ref:
        fail('BOOKING_MINIMUM_SLAB_NOT_CONFIGURED')
    doc = {'inside_slabs':schedule.inside_slabs,'outside_slabs':schedule.outside_slabs}
    if stable_digest(doc) != schedule.schedule_sha256:
        fail('BOOKING_MINIMUM_SCHEDULE_CHANGED')
    slabs = doc['inside_slabs'] if context.relation == 'INSIDE' else doc['outside_slabs']
    if slabs is None:
        fail('BOOKING_MINIMUM_RELATION_NOT_CONFIGURED')
    try:
        minimum = minimum_for_distance(slabs, approved_distance_m)
    except BookingBudgetError as exc:
        fail(exc.code)
    return revision, choice, minimum, doc


def _validate_booking_fare(plan, relation):
    """Deliberately narrow supported billing contract; fail closed otherwise.

    Charging is energy-only with strictly positive rate. Booking fees may
    trigger at UPFRONT or COMPLETED, but the same non-charging component
    must not trigger at both events. Unknown waiting/distance adjustments
    need a separate authorized amendment, never silent after-the-fact debit.
    """
    try:
        profile=plan['profiles'][relation]
        charging=profile['charging']
        if charging['basis'] != 'ENERGY_KWH' or exact(charging['rate'],'energy_rate') <= 0:
            fail('BOOKING_ENERGY_TARIFF_REQUIRED')
        for name in ('visit','distance'):
            part = profile.get(name)
            if part and {'UPFRONT','COMPLETED'} <= set(part['applies_on']):
                fail('BOOKING_DOUBLE_FEE_EVENT')
        for part in profile.get('extras',[]):
            if part['basis']=='WAITING_MINUTE' and any(e in part['applies_on'] for e in ('UPFRONT','COMPLETED')):
                fail('BOOKING_WAITING_FEE_REQUIRES_EXPLICIT_POLICY')
            if {'UPFRONT','COMPLETED'} <= set(part['applies_on']):
                fail('BOOKING_DOUBLE_FEE_EVENT')
    except (KeyError,TypeError,ValueError,CommercialError) as exc:
        fail(getattr(exc,'code','BOOKING_TARIFF_SHAPE_INVALID'))


def _quoted_fees(ctx, catalog, distance_m):
    try:
        upfront=_eval(ctx,catalog,'UPFRONT',_measurements(distance_m,estimated=True))
        completion_zero=_eval(ctx,catalog,'COMPLETED',_measurements(distance_m,estimated=True))
        fixed=safe_total(money(upfront['totals']['gross_minor'],'upfront'),
                         money(completion_zero['totals']['gross_minor'],'completion_fixed'))
    except (CommercialError,BookingBudgetError,TypeError,KeyError) as exc:
        fail(getattr(exc,'code','BOOKING_FIXED_FEE_UNAVAILABLE'))
    return fixed, upfront, completion_zero


def _booking_disclosure(owner, request, proof, now):
    zone=ServiceZone.objects.select_for_update().filter(pk=proof.zone_id,status='PUBLISHED').first()
    if (zone is None or zone.starts_at is not None and zone.starts_at > now or
        zone.ends_at is not None and zone.ends_at <= now):
        fail('BOOKING_ZONE_NOT_PUBLISHED')
    if proof.relation=='OUTSIDE' and zone.restriction_mode=='STRICT':
        fail('BOOKING_STRICT_ZONE_OUTSIDE_NOT_AUTHORIZED')
    if not CommercialZoneChannel.objects.select_for_update().filter(zone=zone,
            channel='ROAMING',enabled=True).exists():
        fail('BOOKING_ZONE_CHANNEL_DISABLED')
    # A provider may tighten a customer's chosen energy, never expand it.
    if proof.total_energy_wh_ceiling > request.requested_energy_wh:
        fail('BOOKING_ENERGY_CEILING_EXCEEDS_CUSTOMER_REQUEST')
    group=_group_for(owner,now)
    a,t,ga,gp,seller,catalog=_catalog(zone,'ROAMING',group,now)
    context=Context(str(zone.pk),'ROAMING',proof.relation,now,seller,
        pricing_group_id=group,serviceable=True,operational=True,verified=True)
    revision, selected, minimum, schedule = _policy_and_minimum(
        zone,context,catalog,proof.approved_distance_m)
    plan=t.get((selected['selection']['tariff_key'],selected['selection']['tariff_version']))
    if not isinstance(plan,dict):
        fail('BOOKING_SELECTED_TARIFF_MISSING')
    _validate_booking_fare(plan,proof.relation)
    fixed, upfront, completed_zero=_quoted_fees(context,catalog,proof.approved_distance_m)
    # The same tariff/GST selection must govern minimum, upfront, completion
    # and charging-only pricing. Otherwise a quoted total is not authoritative.
    for priced in (upfront,completed_zero):
        for key in ('tariff_key','tariff_version','tariff_assignment_id',
                    'gst_profile_id','gst_profile_version','gst_assignment_id'):
            if priced['selection'][key] != selected['selection'][key]:
                fail('BOOKING_TARIFF_GST_SELECTION_CONFLICT')
    fixed_plus_minimum=safe_total(fixed,minimum)
    catalog_ref={
       'context':{'zone_id':str(zone.pk),'channel':'ROAMING','relation':proof.relation,
          'decision_at':_at(now),'seller_ref':seller,'group_id':group},
       'catalog':catalog,
       'selected_tariff':{'policy_id':str(revision.pk),'key':plan['key'],
          'version':plan['version'],'assignment_id':selected['selection']['tariff_assignment_id']},
       'selected_gst':{'profile_id':selected['selection']['gst_profile_id'],
          'version':selected['selection']['gst_profile_version'],
          'assignment_id':selected['selection']['gst_assignment_id'],
          'legal_decision_ref':selected['selection']['legal_decision_ref']},
       'minimum_schedule':schedule,
       'minimum_schedule_sha256':stable_digest(schedule),
       'fixed_preview_upfront':upfront,
       'fixed_preview_completion':completed_zero,
    }
    summary={'request_id':str(request.pk),'owner_id':str(owner.pk),
       'zone_id':str(zone.pk),'relation':proof.relation,
       'approved_distance_m':proof.approved_distance_m,
       'mandatory_noncharging_paise':fixed,'minimum_charging_paise':minimum,
       'mandatory_total_paise':fixed_plus_minimum,
       'buffer_paise':proof.buffer_paise,'currency':'INR',
       'tariff':catalog_ref['selected_tariff'],'gst':catalog_ref['selected_gst'],
       'location_ref':proof.location_ref,'route_ref':proof.route_ref,
       'serviceability_ref':proof.serviceability_ref,
       'max_total_wh':proof.total_energy_wh_ceiling,
       'max_session_wh':proof.per_session_wh_ceiling,
    }
    return summary,catalog_ref


def accept_booking(*, owner, proof: BookingAcceptanceProof, request_key: str,
                   verify_acceptance):
    """Internal real booking acceptance + atomic wallet commitment.

    verify_acceptance MUST prove actual customer assent to the exact summary,
    trusted route/location, legal commerce decision and provider evidence.
    The provider must be server-side, durable, locally verifiable under lock;
    this code never trusts a client claim of verified/OTP status.
    """
    if not getattr(settings,'COW_BOOKING_COMMERCIAL_ACCEPT_ENABLED',False):
        fail('BOOKING_ACCEPTANCE_DISABLED')
    if not callable(verify_acceptance) or not isinstance(proof,BookingAcceptanceProof):
        fail('BOOKING_ACCEPTANCE_PROVIDER_MISSING')
    _require_key(request_key)
    try:
        with transaction.atomic():
            safety.lock_gate()
            now=timezone.now()
            _assert_customer(owner)
            proof.check(now)
            request=ServiceRequest.objects.select_for_update().filter(
                pk=UUID(proof.request_id),customer=owner).first()
            if request is None:
                fail('BOOKING_REQUEST_NOT_FOUND')
            wallet=_wallet_locked(owner)
            existing=CoWBookingCommercialCommitment.objects.select_related('job').filter(
                owner=owner,booking_key=request_key).first()
            if existing:
                frozen=existing.snapshot
                refs=frozen.get('proof_refs',{})
                if (existing.job.request_id!=request.pk or
                    existing.approved_distance_m!=proof.approved_distance_m or
                    existing.protected_buffer_paise!=proof.buffer_paise or
                    frozen.get('max_total_wh')!=proof.total_energy_wh_ceiling or
                    frozen.get('max_session_wh')!=proof.per_session_wh_ceiling or
                    frozen.get('context',{}).get('zone_id')!=proof.zone_id or
                    frozen.get('context',{}).get('relation')!=proof.relation or
                    refs!={'location':proof.location_ref,'route':proof.route_ref,
                           'serviceability':proof.serviceability_ref,
                           'acceptance':proof.acceptance_ref}):
                    fail('BOOKING_ACCEPTANCE_REPLAY_CONFLICT')
                _verify_snap(existing)
                return booking_view(existing)
            summary, extra=_booking_disclosure(owner,request,proof,now)
            request_digest=stable_digest({'summary':summary,'acceptance_ref':proof.acceptance_ref})
            if ServiceJob.objects.filter(request=request).exists():
                fail('BOOKING_ALREADY_ACCEPTED')
            if verify_acceptance(owner,proof,deepcopy(summary),request_digest,now) is not True:
                fail('BOOKING_CUSTOMER_ACCEPTANCE_NOT_VERIFIED')
            other=_active_holds_locked(wallet)
            budget=booking_affordability(wallet.balance_paise,other,
                summary['mandatory_noncharging_paise'],
                summary['minimum_charging_paise'],proof.buffer_paise)
            selected_policy=PricingPolicy.objects.select_for_update().get(
                pk=UUID(extra['selected_tariff']['policy_id']))
            quotation=Quote.objects.create(request=request,pricing_policy=selected_policy,
                pricing_snapshot={'commercial_summary':summary,'request_sha256':request_digest},
                total_paise=summary['mandatory_total_paise'],currency='INR',
                expires_at=now+timedelta(minutes=10),accepted_at=now)
            job=ServiceJob.objects.create(request=request,accepted_quote=quotation,
                state='DISPATCHING')
            frozen={**extra,'job_id':str(job.pk),'owner_id':str(owner.pk),
                'request_id':str(request.pk),'approved_distance_m':proof.approved_distance_m,
                'fixed_fees_paise':budget.mandatory_noncharging_paise,
                'minimum_charging_paise':budget.mandatory_charging_paise,
                'buffer_paise':budget.protected_buffer_paise,
                'max_total_wh':proof.total_energy_wh_ceiling,
                'max_session_wh':proof.per_session_wh_ceiling,
                'proof_refs':{'location':proof.location_ref,'route':proof.route_ref,
                              'serviceability':proof.serviceability_ref,
                              'acceptance':proof.acceptance_ref},
                'accepted_at':_at(now),'accepted_summary_sha256':stable_digest(summary),
            }
            initial_hold=safe_total(budget.mandatory_noncharging_paise,
                budget.mandatory_charging_paise,budget.protected_buffer_paise)
            booking=CoWBookingCommercialCommitment.objects.create(
                job=job,owner=owner,wallet=wallet,booking_key=request_key,
                request_sha256=request_digest,snapshot=frozen,
                snapshot_sha256=stable_digest(frozen),
                fixed_fees_paise=budget.mandatory_noncharging_paise,
                minimum_charging_paise=budget.mandatory_charging_paise,
                protected_buffer_paise=budget.protected_buffer_paise,
                reserved_paise=initial_hold,
                approved_distance_m=proof.approved_distance_m,currency='INR',state='FUNDED')
            _audit(owner,booking,'COW_BOOKING_FUNDED',
                reserved_paise=initial_hold,mandatory_total_paise=summary['mandatory_total_paise'],
                buffer_paise=proof.buffer_paise,request_id=str(request.pk))
            return booking_view(booking)
    except (BookingBudgetError,CommercialError,ChargingCommercialError) as exc:
        fail(getattr(exc,'code','BOOKING_ADMISSION_INVALID'))


def booking_view(booking):
    return {'id':str(booking.pk),'job_id':str(booking.job_id),
        'state':booking.state,'currency':'INR',
        'mandatory_noncharging_paise':booking.fixed_fees_paise,
        'minimum_charging_paise':booking.minimum_charging_paise,
        'protected_buffer_paise':booking.protected_buffer_paise,
        'wallet_held_paise':booking.reserved_paise,
        'approved_distance_m':booking.approved_distance_m,
        'chargeable_since':booking.snapshot.get('accepted_at'),
        'maximum_session_wh':booking.snapshot.get('max_session_wh'),
        'tariff':deepcopy(booking.snapshot.get('selected_tariff')),
        'tax':deepcopy(booking.snapshot.get('selected_gst'))}


def _accepted_completed_energy(booking):
    """Only complete, identity-bound physical facts count toward next cap.

    Any attempted-but-unresolved start, stop or completion blocks an additional
    OCPP transaction. Fact ordering/identity is exclusively owned by HAL V1.
    """
    energy = 0
    receipt_refs = []
    allocations = list(CoWBookingChargingAllocation.objects.select_for_update().filter(
        booking=booking).select_related('permit').order_by('created_at','pk'))
    for allocation in allocations:
        permit=allocation.permit
        if allocation.state=='CANCELLED_UNSENT':
            cmd=HALV1ChargingCommand.objects.select_for_update().filter(
                intent__permit=permit,kind='START').first()
            if (cmd is None or cmd.state!='EXPIRED' or cmd.attempted_at is not None or
                HALV1ChargingSession.objects.filter(intent__permit=permit).exists() or
                permit.state!='RELEASED'):
                fail('BOOKING_UNSENT_RECOVERY_EVIDENCE_CONFLICT')
            continue
        intents=list(HALV1ChargingIntent.objects.select_for_update().filter(
            permit=permit)[:2])
        if len(intents) != 1:
            fail('BOOKING_PREVIOUS_START_UNPROVEN')
        intent=intents[0]
        sessions=list(HALV1ChargingSession.objects.select_for_update().filter(
            intent=intent)[:2])
        if len(sessions) != 1:
            fail('BOOKING_PREVIOUS_PHYSICAL_RESULT_PENDING')
        s=sessions[0]
        if (s.state != 'PHYSICALLY_COMPLETED' or s.completion_fact_id is None or
            s.meter_end_wh is None or s.meter_end_wh < s.meter_start_wh or
            s.stopped_at is None or s.stopped_at < s.started_at or
            permit.state != 'SETTLEMENT_PENDING' or
            intent.state != 'ACTUALLY_STARTED'):
            fail('BOOKING_PREVIOUS_CHARGE_REQUIRES_RECONCILIATION')
        stop=HALV1ChargingCommand.objects.select_for_update().filter(
            intent=intent,kind='STOP').first()
        if stop is not None and stop.state not in {'MATERIALIZED','REJECTED','EXPIRED'}:
            fail('BOOKING_PREVIOUS_STOP_UNRESOLVED')
        receipt=HALV1FactReceipt.objects.filter(fact_id=s.completion_fact_id,
            fact_type='transaction.completed',scope_id=permit.scope_id,
            charger_id=permit.charger_id,processing_state='APPLIED').first()
        if receipt is None:
            fail('BOOKING_COMPLETION_RECEIPT_UNPROVEN')
        delta=s.meter_end_wh-s.meter_start_wh
        if delta > allocation.authorized_session_wh:
            fail('BOOKING_PHYSICAL_LIMIT_OVERSHOT')
        energy+=delta
        if energy >= 2**63:
            fail('BOOKING_CUMULATIVE_ENERGY_OVERFLOW')
        receipt_refs.append({'session_id':str(s.pk),'completion_fact_id':str(s.completion_fact_id),
            'wh':delta,'meter_start_wh':s.meter_start_wh,'meter_end_wh':s.meter_end_wh,
            'hal_transaction_id':str(s.hal_transaction_id),
            'stopped_at':_at(s.stopped_at)})
    return energy,receipt_refs


def _price_energy(snapshot, energy_wh, *, final=False):
    if type(energy_wh) is not int or not 0 <= energy_wh < 2**63:
        raise BookingBudgetError('ENERGY_QUANTITY_INVALID')
    ctx=_ctx(snapshot)
    measurements=_measurements(snapshot['approved_distance_m'],energy_wh=energy_wh,
                               estimated=not final)
    result=_eval(ctx,_only_charging(snapshot['catalog']),'COMPLETED',measurements,
                 'FINAL' if final else 'PREVIEW')
    chosen=result['selection']
    for key,wanted in (('tariff_key',snapshot['selected_tariff']['key']),
        ('tariff_version',snapshot['selected_tariff']['version']),
        ('tariff_assignment_id',snapshot['selected_tariff']['assignment_id']),
        ('gst_profile_id',snapshot['selected_gst']['profile_id']),
        ('gst_profile_version',snapshot['selected_gst']['version']),
        ('gst_assignment_id',snapshot['selected_gst']['assignment_id'])):
        if chosen[key]!=wanted:
            raise BookingBudgetError('FROZEN_TARIFF_SELECTION_CHANGED')
    if not all(line['component']=='CHARGING' for line in result['lines']):
        raise BookingBudgetError('NONCHARGING_AMOUNT_IN_ENERGY_PRICE')
    return money(result['totals']['gross_minor'],'charging_price'), result


def _start_proof_digest(proof):
    return stable_digest({'job_id':proof.job_id,'owner_id':proof.owner_id,
        'charger_id':proof.charger_id,'connector_id':proof.connector_id,
        'duty_id':proof.duty_id,'verified_at':_at(proof.verified_at),
        'arrival_ref':proof.arrival_location_ref,'duty_ref':proof.duty_ref,
        'otp_ref':proof.otp_consumed_ref,'consent_ref':proof.customer_consent_ref})


def prepare_and_stage_start(*, owner, booking_id, proof: StartHandoverProof,
                            start_key: str, verify_handover):
    """The customer clicked Start; prepare an exactly funded physical cap.

    The trusted handover provider verifies BOTH current location-based arrival
    and consumed, job-bound OTP/customer consent. The same transaction stages
    the immutable HAL command; the existing HAL dispatcher sends it afterward.
    No network IO occurs here.
    """
    if not (getattr(settings,'COW_BOOKING_COMMERCIAL_START_ENABLED',False) and
            getattr(settings,'COW_HAL_V1_NEW_STARTS_ENABLED',False)):
        fail('BOOKING_CHARGING_STARTS_DISABLED')
    if not isinstance(proof,StartHandoverProof) or not callable(verify_handover):
        fail('BOOKING_HANDOVER_PROVIDER_MISSING')
    _require_key(start_key)
    try:
        with transaction.atomic():
            safety.lock_gate()
            now=timezone.now()
            _assert_customer(owner)
            proof.check(now)
            booking=CoWBookingCommercialCommitment.objects.select_for_update().select_related(
                'job__request','wallet').filter(pk=booking_id,owner=owner).first()
            if booking is None:
                fail('BOOKING_NOT_FOUND')
            _verify_snap(booking)
            if booking.protected_buffer_paise <= 0:
                fail('BOOKING_BUFFER_NOT_APPROVED','Approved positive safety buffer required')
            if proof.job_id != str(booking.job_id) or proof.owner_id != str(owner.pk):
                fail('BOOKING_HANDOVER_OWNER_MISMATCH')
            job=ServiceJob.objects.select_for_update().filter(pk=booking.job_id).first()
            if job is None or job.request.customer_id!=owner.pk or job.driver_duty_id is None:
                fail('BOOKING_DUTY_NOT_ASSIGNED')
            if proof.duty_id != str(job.driver_duty_id):
                fail('BOOKING_DUTY_PROOF_MISMATCH')
            if job.state not in {'SERVICE_PREPARING','CHARGING'}:
                fail('BOOKING_NOT_READY_FOR_CHARGING')
            if booking.state not in {'FUNDED','START_PENDING','IN_SERVICE'}:
                fail('BOOKING_COMMERCIAL_NOT_OPEN')
            proof_digest=_start_proof_digest(proof)
            old=CoWBookingChargingAllocation.objects.select_related('permit').filter(
                booking=booking,start_key=start_key).first()
            if old is not None:
                if old.start_sha256 != proof_digest:
                    fail('BOOKING_START_REPLAY_CONFLICT')
                intent=HALV1ChargingIntent.objects.filter(permit=old.permit).first()
                if intent is None or intent.idempotency_key != start_key:
                    fail('BOOKING_START_REPLAY_MISSING')
                return v1_charging.intent_view(intent)
            charger=Charger.objects.select_for_update().filter(pk=UUID(proof.charger_id)).first()
            connector=ChargerConnector.objects.select_for_update().filter(
                pk=UUID(proof.connector_id),charger=charger,active=True).first() if charger else None
            if (charger is None or connector is None or not charger.active or
                charger.operational_status!='AVAILABLE'):
                fail('BOOKING_CHARGER_UNAVAILABLE')
            # Preserve driver, vehicle, duty, pairing, legacy HAL, and connector guards.
            safety.assert_start_admissible(job.pk,charger.pk,connector.connector_number,
                                           allow_followup=(job.state=='CHARGING'))
            if verify_handover(owner,booking,proof,now) is not True:
                fail('BOOKING_LOCATION_OR_OTP_NOT_VERIFIED')
            mapping=HALV1Mapping.objects.select_for_update().filter(charger=charger,
                scope_id=UUID(v1_machine.scope()),state='SYNCHRONIZED').first()
            if mapping is None or mapping.snapshot != v1_machine.mapping_payload(charger):
                fail('BOOKING_HAL_MAPPING_UNAVAILABLE')
            previous,receipts=_accepted_completed_energy(booking)
            wallet=_wallet_locked(owner)
            if wallet.pk!=booking.wallet_id:
                fail('BOOKING_WALLET_OWNER_CHANGED')
            unrelated=_active_holds_locked(wallet,exclude_booking_id=booking.pk)
            # The current booking hold is already part of W. Do not subtract it
            # again; other active holds are excluded exactly once.
            available=wallet.balance_paise-unrelated
            if available < booking.reserved_paise:
                fail('BOOKING_WALLET_HOLD_DEFICIT')
            decision=booking_affordability(wallet.balance_paise,unrelated,
                booking.fixed_fees_paise,booking.minimum_charging_paise,
                booking.protected_buffer_paise)
            frozen=booking.snapshot
            cap=determine_cap(
                price_gross_paise=lambda q:_price_energy(frozen,q)[0],
                money_state=decision,previously_completed_wh=previous,
                maximum_total_wh=frozen['max_total_wh'],
                maximum_per_session_wh=frozen['max_session_wh'])
            # Reserve ALL currently approved booking exposure + buffer, so
            # another booking cannot spend unused capacity before final debit.
            new_hold=wallet.balance_paise-unrelated
            if new_hold<booking.reserved_paise:
                fail('BOOKING_WALLET_EXPOSURE_DECREASED')
            booking.reserved_paise=new_hold
            booking.state='START_PENDING'
            booking.save(update_fields=['reserved_paise','state','updated_at'])
            permit_id=uuid4()
            reference=f'cow:booking:{booking.pk}:start:{permit_id}'
            commercial={
                'state':'COMMITTED','wallet_hold_state':'HELD',
                'source':'COW_BOOKING_COMMERCIAL_V2','hold_reference':reference,
                'booking_id':str(booking.pk),'booking_digest':booking.snapshot_sha256,
                'tariff':deepcopy(frozen['selected_tariff']),
                'tax':deepcopy(frozen['selected_gst']),
                'budget_gross_paise':cap.charging_budget_paise,
                'mandatory_charging_paise':booking.minimum_charging_paise,
                'mandatory_wh':cap.mandatory_wh,
                'extra_wh':cap.extra_wh,
                'previous_completed_wh':cap.previous_wh,
                'authorized_cumulative_wh':cap.cumulative_wh,
                'authorized_session_wh':cap.physical_session_wh,
                'buffer_paise':booking.protected_buffer_paise,
                'handover_sha256':proof_digest,
            }
            permit=HALV1ChargingPermit.objects.create(
                id=permit_id,scope_id=UUID(v1_machine.scope()),owner=owner,
                channel='ROAMING',job=job,charger=charger,connector=connector,
                wallet_hold_reference=reference,
                commercial_snapshot=commercial,
                location_evidence={'verified':True,'provider_ref':proof.arrival_location_ref,
                    'handover_sha256':proof_digest,'verified_at':_at(proof.verified_at)},
                operational_evidence={'verified':True,'provider_ref':proof.duty_ref,
                    'otp_consumed_ref':proof.otp_consumed_ref,
                    'customer_consent_ref':proof.customer_consent_ref},
                limit_type='AUTO',energy_limit_wh=cap.physical_session_wh,
                energy_limit_source='WALLET',
                max_duration_seconds=0,duration_limit_source='NONE',
                expires_at=now+timedelta(minutes=4),state='RESERVED')
            allocation=CoWBookingChargingAllocation.objects.create(
                booking=booking,permit=permit,start_key=start_key,
                start_sha256=proof_digest,authorized_prior_wh=previous,
                authorized_session_wh=cap.physical_session_wh,
                authorized_total_wh=cap.cumulative_wh,
                budget_gross_paise=cap.charging_budget_paise,
                proof_sha256=proof_digest,state='PREPARED')
            # This creates the one-use credential and durable START command.
            # Only the existing HAL worker performs subsequent network IO.
            output=v1_charging.stage_start(owner,permit.pk,start_key)
            _audit(owner,booking,'COW_BOOKING_CHARGING_START_STAGED',
                start_intent_id=output['id'],permit_id=str(permit.pk),
                previous_completed_wh=previous,
                mandatory_wh=cap.mandatory_wh,extra_wh=cap.extra_wh,
                session_limit_wh=cap.physical_session_wh,
                booking_hold_paise=new_hold,completed_receipts=len(receipts))
            return {**output,'commercial':{
                'booking_id':str(booking.pk),'limit_wh':cap.physical_session_wh,
                'mandatory_wh':cap.mandatory_wh,'extra_wh':cap.extra_wh,
                'previous_completed_wh':previous,
                'source':'WALLET','new_booking_required':False}}
    except (CommercialError,BookingBudgetError,ChargingCommercialError) as exc:
        fail(getattr(exc,'code','BOOKING_CHARGING_AUTHORIZATION_INVALID'))


def booking_financial_view(owner, booking_id):
    booking=CoWBookingCommercialCommitment.objects.filter(pk=booking_id,owner=owner).first()
    if booking is None:
        fail('BOOKING_NOT_FOUND')
    _verify_snap(booking)
    result=booking_view(booking)
    final=CoWBookingFinancialSettlement.objects.filter(booking=booking).first()
    result['financial_state']=final.state if final else 'NOT_SETTLED'
    result['final_gross_paise']=final.final_gross_paise if final else None
    result['posted_wallet_balance_paise']=None
    # A real posted balance belongs to GET /wallet; never assert a read-only
    # projected financial hold is actual deducted money.
    allocations=CoWBookingChargingAllocation.objects.filter(booking=booking)
    result['physical_start_count']=allocations.count()
    result['last_physical_permit_id']=str(allocations.order_by('-created_at').first().permit_id) if allocations.exists() else None
    return result


def _minimum_adjustment_gst(snapshot, adjustment):
    _,tariffs,_,gsts=_rehydrate(snapshot['catalog'])
    selected=snapshot['selected_tariff']
    plan=tariffs[(selected['key'],selected['version'])]
    charge=plan['profiles'][snapshot['context']['relation']]['charging']
    tax=gsts[(snapshot['selected_gst']['profile_id'],snapshot['selected_gst']['version'])]
    tax_class=charge['tax_class']
    rule=tax.tax_classes.get(tax_class)
    if rule is None or tax.status!='APPROVED':
        raise BookingBudgetError('MINIMUM_TAX_CLASS_UNAPPROVED')
    rates={h:exact(rule['rates'][h],h,max_value='100') for h in HEADS}
    net,heads,gross=split_tax(adjustment,rates,'INCLUSIVE')
    if gross!=adjustment or net+sum(heads.values())!=adjustment:
        raise BookingBudgetError('MINIMUM_TAX_RECONCILIATION')
    return {'gross_paise':gross,'net_paise':net,'tax_heads_paise':heads,
            'tax_class':tax_class,'supply_group':rule['supply_group'],
            'treatment':rule['treatment'],
            'source':'ONE_BOOKING_MINIMUM_PURCHASE_SHORTFALL',
            'not_metered_energy':True}


def settle_completed_booking(*, booking_id, verify_driver_completion, completion_ref,
                             actor=None):
    """Single actual debit at legitimate job completion; one job ledger receipt.

    This MUST be called by the driver booking-completion pipeline only after
    authoritative job state transition, or by a locked, permissioned reconciler.
    verify_driver_completion proves earned fixed fees, minimum and actual service
    evidence. No implied cancellation charge or arbitrary forgiveness.
    """
    if not (getattr(settings,'COW_BOOKING_COMMERCIAL_SETTLEMENT_ENABLED',False) and
            getattr(settings,'COW_HAL_V1_FINANCIAL_POSTING_ENABLED',False)):
        fail('BOOKING_FINANCIAL_POSTING_DISABLED')
    if not callable(verify_driver_completion):
        fail('BOOKING_FINAL_PROOF_PROVIDER_MISSING')
    _valid_ref(completion_ref)
    try:
        with transaction.atomic():
            if actor is not None:
                safety.fresh_admin(actor,'payments.manage')
            safety.lock_gate()
            booking=CoWBookingCommercialCommitment.objects.select_for_update().select_related(
                'job__request','wallet').filter(pk=booking_id).first()
            if booking is None:
                fail('BOOKING_NOT_FOUND')
            _verify_snap(booking)
            prior=CoWBookingFinancialSettlement.objects.select_for_update().filter(booking=booking).first()
            if prior is not None and prior.state=='POSTED':
                return {'state':'POSTED','replayed':True,'total_paise':prior.final_gross_paise,
                        'settlement_id':str(prior.pk)}
            if prior is not None and (prior.state!='RECONCILIATION_REQUIRED' or
                                     prior.reason!='EXPOSURE_OR_WALLET_SHORTFALL' or
                                     prior.wallet_transaction_id is not None):
                fail('BOOKING_FINANCIAL_RECONCILIATION_REQUIRED')
            job=ServiceJob.objects.select_for_update().filter(pk=booking.job_id).first()
            if job is None or job.state!='COMPLETED' or job.request.customer_id!=booking.owner_id:
                fail('BOOKING_NOT_DRIVER_COMPLETED')
            if booking.state not in {'FUNDED','START_PENDING','IN_SERVICE','CLOSING'} and not (
                booking.state=='RECONCILIATION_REQUIRED' and prior is not None and
                prior.reason=='EXPOSURE_OR_WALLET_SHORTFALL'):
                fail('BOOKING_COMMERCIAL_NOT_SETTLEABLE')
            delivered,receipts=_accepted_completed_energy(booking)
            if delivered > booking.snapshot['max_total_wh']:
                fail('BOOKING_METERED_ENERGY_OVER_LIMIT')
            charging_gross,charging_breakdown=_price_energy(booking.snapshot,delivered,final=True)
            minimum_shortfall=max(0,booking.minimum_charging_paise-charging_gross)
            minimum_tax=_minimum_adjustment_gst(booking.snapshot,minimum_shortfall)
            total=safe_total(booking.fixed_fees_paise,charging_gross,minimum_shortfall)
            final={'job_id':str(job.pk),'booking_id':str(booking.pk),
                'completion_ref':completion_ref,'currency':'INR',
                'physical_receipts':receipts,'delivered_wh':delivered,
                'fixed_fees_paise':booking.fixed_fees_paise,
                'minimum_charging_paise':booking.minimum_charging_paise,
                'charging_gross_paise':charging_gross,
                'minimum_adjustment':minimum_tax,
                'total_gross_paise':total,'charging_breakdown':charging_breakdown,
                'pricing_snapshot_sha256':booking.snapshot_sha256,
                'not_a_tax_invoice':True}
            # The verifier must prove this booking's customer/driver handover,
            # real delivered service, closure and which fees were actually
            # earned. No arbitrary client/admin-supplied boolean is accepted.
            if verify_driver_completion(booking,job,deepcopy(final)) is not True:
                fail('BOOKING_FINANCIAL_CLOSURE_NOT_VERIFIED')
            wallet=_wallet_locked(booking.owner)
            if wallet.pk!=booking.wallet_id:
                fail('BOOKING_WALLET_CHANGED')
            other=_active_holds_locked(wallet,exclude_booking_id=booking.pk)
            if wallet.balance_paise < other+booking.reserved_paise:
                fail('BOOKING_WALLET_RESERVATION_DAMAGED')
            final_sha=stable_digest(final)
            if prior is not None and (prior.final_sha256!=final_sha or
                prior.final_snapshot!=final or prior.final_gross_paise!=total or
                prior.delivered_wh!=delivered):
                fail('BOOKING_FINANCIAL_RECONCILIATION_SNAPSHOT_CHANGED')
            usable_hold=booking.reserved_paise-booking.protected_buffer_paise
            # A verified top-up may restore a previously held deficit. Only the
            # existing immutable financial claim may expand under a wallet row lock.
            # No new HAL command or repricing is authorized by this recovery.
            if prior is not None and total>usable_hold:
                required=safe_total(total,booking.protected_buffer_paise)
                if wallet.balance_paise-other>=required:
                    booking.reserved_paise=required
                    booking.save(update_fields=['reserved_paise','updated_at'])
                    usable_hold=total
            if usable_hold<0 or total>usable_hold or total>wallet.balance_paise-other:
                if prior is not None:
                    return {'state':'RECONCILIATION_REQUIRED','total_paise':total,
                            'settlement_id':str(prior.pk),'replayed':True}
                held=CoWBookingFinancialSettlement.objects.create(
                    booking=booking,final_gross_paise=total,
                    fixed_fees_paise=booking.fixed_fees_paise,
                    charging_gross_paise=charging_gross,
                    minimum_adjustment_paise=minimum_shortfall,
                    delivered_wh=delivered,final_snapshot=final,final_sha256=final_sha,
                    state='RECONCILIATION_REQUIRED',reason='EXPOSURE_OR_WALLET_SHORTFALL')
                booking.state='RECONCILIATION_REQUIRED'
                booking.save(update_fields=['state','updated_at'])
                _audit(booking.owner,booking,'COW_BOOKING_SETTLEMENT_HELD',
                    total_paise=total,protected_buffer_paise=booking.protected_buffer_paise,
                    reserved_paise=booking.reserved_paise)
                return {'state':'RECONCILIATION_REQUIRED','total_paise':total,
                        'settlement_id':str(held.pk)}
            posted=wallet_ledger.debit_booking_settlement(
                wallet,booking,total,final_sha,str(job.pk))
            if prior is not None:
                prior.wallet_transaction=posted
                prior.state='POSTED'
                prior.reason=''
                prior.save(update_fields=['wallet_transaction','state','reason','updated_at'])
                row=prior
            else:
                row=CoWBookingFinancialSettlement.objects.create(
                    booking=booking,final_gross_paise=total,
                fixed_fees_paise=booking.fixed_fees_paise,
                charging_gross_paise=charging_gross,
                minimum_adjustment_paise=minimum_shortfall,
                delivered_wh=delivered,final_snapshot=final,
                final_sha256=final_sha,wallet_transaction=posted,state='POSTED')
            booking.state='SETTLED';booking.reserved_paise=0
            booking.save(update_fields=['state','reserved_paise','updated_at'])
            _audit(booking.owner,booking,'COW_BOOKING_FINANCIAL_SETTLED',
                gross_paise=total,settlement_id=str(row.pk),
                wallet_transaction_id=str(posted.pk),physical_sessions=len(receipts))
            return {'state':'POSTED','total_paise':total,
                    'settlement_id':str(row.pk),'replayed':False}
    except (BookingBudgetError,CommercialError,ChargingCommercialError) as exc:
        fail(getattr(exc,'code','BOOKING_FINANCIAL_DECISION_INVALID'))


def cancel_unstarted_booking(*, booking_id, verify_zero_liability, cancellation_ref):
    """Release only if there has been NO physical start or earned booking fee.

    A cancelled job alone is not evidence that transport or a visit was free.
    Any attempted HAL start blocks this automated release forever until an
    independently authorized reconciliation confirms no money is owed.
    """
    if not callable(verify_zero_liability):
        fail('BOOKING_CANCELLATION_PROVIDER_MISSING')
    _valid_ref(cancellation_ref)
    with transaction.atomic():
        safety.lock_gate()
        booking=CoWBookingCommercialCommitment.objects.select_for_update().select_related(
            'job').filter(pk=booking_id).first()
        if booking is None:
            fail('BOOKING_NOT_FOUND')
        _verify_snap(booking)
        if booking.state=='CANCELLED':
            return {'state':'CANCELLED','replayed':True}
        if booking.state!='FUNDED' or booking.job.state!='CANCELLED' or \
                CoWBookingChargingAllocation.objects.filter(booking=booking).exists():
            fail('BOOKING_CANCELLATION_REQUIRES_REVIEW')
        if verify_zero_liability(booking,booking.job,cancellation_ref) is not True:
            fail('BOOKING_CANCELLATION_FINANCIAL_PROOF_MISSING')
        _wallet_locked(booking.owner)  # serialize release against charging/other funds
        booking.state='CANCELLED';booking.reserved_paise=0
        booking.save(update_fields=['state','reserved_paise','updated_at'])
        _audit(booking.owner,booking,'COW_BOOKING_UNSTARTED_RELEASED',
               cancellation_ref=cancellation_ref)
        return {'state':'CANCELLED','replayed':False}


def settled_booking_statement(owner, booking_id):
    booking=CoWBookingCommercialCommitment.objects.filter(pk=booking_id,owner=owner).first()
    if booking is None:
        fail('BOOKING_NOT_FOUND')
    row=CoWBookingFinancialSettlement.objects.filter(booking=booking,state='POSTED').first()
    if row is None or stable_digest(row.final_snapshot)!=row.final_sha256:
        fail('BOOKING_SETTLEMENT_STATEMENT_UNAVAILABLE')
    return {'title':'Commercial Charging Statement','status':'SETTLED',
        'not_a_gst_tax_invoice':True,'currency':'INR',
        'booking_id':str(booking.pk),'job_id':str(booking.job_id),
        'final_total_paise':row.final_gross_paise,
        'fixed_fees_paise':row.fixed_fees_paise,
        'charging_gross_paise':row.charging_gross_paise,
        'minimum_adjustment_paise':row.minimum_adjustment_paise,
        'delivered_wh':row.delivered_wh,
        'tax_on_minimum_adjustment':deepcopy(row.final_snapshot['minimum_adjustment']),
        'charging_breakdown':deepcopy(row.final_snapshot['charging_breakdown']),
        'physical_receipts':deepcopy(row.final_snapshot['physical_receipts']),
        'wallet_transaction_id':str(row.wallet_transaction_id)}


def reflect_hal_fact_locked(fact_type, payload, business_state):
    """Same-transaction booking projection for validated HAL V1 facts.

    Physical fact acceptance is HAL's authority. This function cannot change
    metering truth or initiate any debit/stop. A cap overrun keeps the physical
    completion accepted but quarantines all next-start and financial actions.
    """
    if fact_type not in {'transaction.started','transaction.completed',
                         'transaction.meter','transaction.soc','command.updated'}:
        return
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Booking fact projection requires the HAL receipt transaction')
    intent=None
    if fact_type=='command.updated':
        raw=payload.get('cms_command_id')
        try:
            command=HALV1ChargingCommand.objects.select_related('intent').filter(pk=UUID(str(raw))).first()
            intent=command.intent if command else None
        except (ValueError,TypeError):
            return
    else:
        try:
            intent=HALV1ChargingIntent.objects.filter(pk=UUID(str(payload.get('cms_start_intent_id')))).first()
        except (ValueError,TypeError):
            return
    if intent is None:
        return
    allocation=CoWBookingChargingAllocation.objects.select_for_update().filter(
        permit_id=intent.permit_id).first()
    if allocation is None:
        return
    booking=CoWBookingCommercialCommitment.objects.select_for_update().filter(
        pk=allocation.booking_id).first()
    if booking is None:
        raise BookingBudgetError('BOOKING_ALLOCATED_JOB_MISSING')
    if business_state=='RECONCILIATION_REQUIRED':
        if booking.state not in {'SETTLED','CANCELLED'}:
            booking.state='RECONCILIATION_REQUIRED'
            booking.save(update_fields=['state','updated_at'])
        allocation.state='RECONCILIATION_REQUIRED'
        allocation.save(update_fields=['state','updated_at'])
        return
    if business_state!='APPLIED':
        return
    if fact_type=='transaction.started':
        # A physical OCPP start also advances the booking's operational job.
        # Never infer it from staging/ack; only from an applied HAL start fact.
        job=ServiceJob.objects.select_for_update().filter(pk=booking.job_id).first()
        if job is None or job.state not in {'SERVICE_PREPARING','CHARGING'}:
            allocation.state='RECONCILIATION_REQUIRED'
            allocation.save(update_fields=['state','updated_at'])
            if booking.state not in {'SETTLED','CANCELLED'}:
                booking.state='RECONCILIATION_REQUIRED'
                booking.save(update_fields=['state','updated_at'])
            return
        if job.state=='SERVICE_PREPARING':
            job_lifecycle.transition_locked(job,'CHARGING','HAL_VERIFIED_PHYSICAL_START',
                {'allocation_id':str(allocation.pk),'intent_id':str(intent.pk),
                 'charger_id':str(allocation.permit.charger_id)})
        if allocation.state=='PREPARED':
            allocation.state='STARTED'
            allocation.save(update_fields=['state','updated_at'])
        if booking.state=='START_PENDING':
            booking.state='IN_SERVICE'
            booking.save(update_fields=['state','updated_at'])
    elif fact_type=='transaction.completed':
        session=HALV1ChargingSession.objects.select_for_update().filter(intent=intent).first()
        if session is None or session.meter_end_wh is None:
            raise BookingBudgetError('BOOKING_COMPLETION_SESSION_MISSING')
        consumed=session.meter_end_wh-session.meter_start_wh
        if consumed<0 or consumed>allocation.authorized_session_wh:
            allocation.state='RECONCILIATION_REQUIRED'
            allocation.save(update_fields=['state','updated_at'])
            booking.state='RECONCILIATION_REQUIRED'
            booking.save(update_fields=['state','updated_at'])
            _audit(booking.owner,booking,'COW_BOOKING_ENERGY_LIMIT_OVERSHOOT',
                session_id=str(session.pk),delivered_wh=consumed,
                authorized_session_wh=allocation.authorized_session_wh)
        elif allocation.state!='RECONCILIATION_REQUIRED':
            allocation.state='COMPLETED'
            allocation.save(update_fields=['state','updated_at'])
            if booking.state=='START_PENDING':
                booking.state='IN_SERVICE'
                booking.save(update_fields=['state','updated_at'])


def recover_expired_unattempted_start(permit_id, *, actor=None):
    """Release only a start conclusively never handed to the network.

    A command with attempted_at is *never* inferred safe from age/404/timeout.
    Reprice residual already-delivered charging before reducing its hold.
    """
    with transaction.atomic():
        if actor is not None:
            safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        allocation=CoWBookingChargingAllocation.objects.select_for_update().select_related(
            'booking','permit').filter(permit_id=permit_id).first()
        if allocation is None:
            fail('BOOKING_UNSENT_ALLOCATION_NOT_FOUND')
        if allocation.state=='CANCELLED_UNSENT':
            return {'state':'CANCELLED_UNSENT','replayed':True}
        permit=HALV1ChargingPermit.objects.select_for_update().get(pk=permit_id)
        booking=CoWBookingCommercialCommitment.objects.select_for_update().get(pk=allocation.booking_id)
        _verify_snap(booking)
        if booking.state not in {'START_PENDING','IN_SERVICE'}:
            fail('BOOKING_UNSENT_RELEASE_NOT_ALLOWED')
        intent=HALV1ChargingIntent.objects.select_for_update().filter(permit=permit).first()
        if intent is None or HALV1ChargingSession.objects.filter(intent=intent).exists():
            fail('BOOKING_UNSENT_RESULT_UNCERTAIN')
        command=HALV1ChargingCommand.objects.select_for_update().filter(
            intent=intent,kind='START').first()
        if (command is None or command.attempted_at is not None or
            command.state not in {'PERSISTED','EXPIRED'} or
            intent.state not in {'PERSISTED','EXPIRED'} or
            permit.state not in {'BOUND','RECONCILIATION_REQUIRED'} or
            timezone.now()<permit.expires_at):
            fail('BOOKING_UNSENT_NETWORK_STATUS_UNPROVEN')
        # Claims of no network attempt are grounded in our durable send-once
        # marker. If a previous send had begun, attempted_at cannot be absent.
        command.state='EXPIRED';command.failure_code='BOOKING_START_EXPIRED_UNSENT'
        command.outcome_at=timezone.now()
        command.save(update_fields=['state','failure_code','outcome_at','updated_at'])
        intent.state='EXPIRED';intent.save(update_fields=['state','updated_at'])
        permit.state='RELEASED';permit.save(update_fields=['state','updated_at'])
        allocation.state='CANCELLED_UNSENT';allocation.save(update_fields=['state','updated_at'])
        done,_=_accepted_completed_energy(booking)
        charged,_=_price_energy(booking.snapshot,done)
        base=safe_total(booking.fixed_fees_paise,
                        max(charged,booking.minimum_charging_paise),
                        booking.protected_buffer_paise)
        wallet=_wallet_locked(booking.owner)
        unrelated=_active_holds_locked(wallet,exclude_booking_id=booking.pk)
        if wallet.balance_paise<unrelated+base:
            fail('BOOKING_UNSENT_HOLD_NOW_UNDERFUNDED')
        booking.reserved_paise=base
        booking.state='IN_SERVICE' if done>0 else 'FUNDED'
        booking.save(update_fields=['reserved_paise','state','updated_at'])
        _audit(booking.owner,booking,'COW_BOOKING_UNSENT_START_RECOVERED',
            permit_id=str(permit_id),remaining_hold_paise=base)
        return {'state':'CANCELLED_UNSENT','replayed':False,
                'remaining_booking_hold_paise':base}
