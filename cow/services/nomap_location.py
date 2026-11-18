"""Authenticated, maps-independent CoW location ingest and tracking projection.

This module establishes provenance (who submitted, for which live duty/job),
sequence, freshness, declared accuracy and plausibility. It cannot prove GPS
hardware authenticity. Phone coordinates are not a substitute for OTP/consent.
"""
from __future__ import annotations
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from cow.common.errors import reject
from cow.domain.location_geometry import decimal_coordinate, geodesic_m, classify_radius, GeometryError
from cow.models import CoWLocationObservation, ServiceJob, ServiceZone
from cow.services.charging_commercial_policy import stable_digest

TRACKABLE = frozenset({'ASSIGNED','ACCEPTED','EN_ROUTE','ARRIVED','SERVICE_PREPARING','CHARGING','WRAP_UP'})
Q7=Decimal('0.0000001')


def _gate():
    if not getattr(settings,'COW_LOCATION_RECEIVE_ENABLED',False):
        reject(503,'LOCATION_RECEIVE_DISABLED','Location ingestion is not authorized')


def _principal(actor):
    if getattr(actor,'plane',None) not in {'driver','app'} or actor.status!='ACTIVE':
        reject(403,'LOCATION_ACTOR_INELIGIBLE','Active driver/customer principal required')


def _authorization(job,actor):
    if job.state not in TRACKABLE:
        reject(409,'LOCATION_JOB_NOT_ACTIVE','This job is not accepting location updates')
    if actor.plane=='driver':
        duty=job.driver_duty
        if (duty is None or duty.driver.principal_id!=actor.pk or
            duty.ended_at is not None or duty.state not in {'AVAILABLE'}):
            reject(403,'LOCATION_DRIVER_DUTY_NOT_ACTIVE','Driver has no matching active duty')
        return
    if job.request.customer_id != actor.pk:
        reject(403,'LOCATION_CUSTOMER_NOT_OWNER','Customer does not own job')


def _positive(name,value,max_value):
    if type(value) is not int or not 0<value<=max_value:
        reject(422,'LOCATION_'+name.upper()+'_INVALID',name+' outside allowed range')
    return value


def _time(value,now):
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        reject(422,'LOCATION_TIMESTAMP_INVALID','Timezone-aware observation required')
    if value > now+timedelta(seconds=10) or now-value>timedelta(seconds=120):
        reject(422,'LOCATION_OBSERVATION_STALE','Observation outside allowed clock window')
    return value


def _location_public(row,*,show_coordinates):
    return {'observation_id':str(row.pk),'sequence':row.sequence,
            'observed_at':row.observed_at.isoformat(),
            'received_at':row.received_at.isoformat(),
            'accuracy_m':row.accuracy_m,
            'latitude':str(row.latitude) if show_coordinates else None,
            'longitude':str(row.longitude) if show_coordinates else None,
            'method':'DEVICE_REPORTED_GPS','trusted_hardware_location':False}


def ingest(*,actor,job_id,latitude,longitude,accuracy_m,observed_at,sequence,client_event_id):
    _gate();_principal(actor)
    _positive('sequence',sequence,(1<<63)-1)
    _positive('accuracy',accuracy_m,200)
    try:
        lat=decimal_coordinate(latitude,'latitude').quantize(Q7,rounding=ROUND_HALF_UP)
        lon=decimal_coordinate(longitude,'longitude').quantize(Q7,rounding=ROUND_HALF_UP)
        client_id=UUID(str(client_event_id))
        if client_id.int==0 or str(client_id)!=str(client_event_id):
            raise ValueError('UUID must be canonical')
    except (GeometryError,ValueError,TypeError,AttributeError) as exc:
        reject(422,'LOCATION_COORDINATE_OR_EVENT_INVALID',str(exc))
    with transaction.atomic():
        now=timezone.now()
        job=ServiceJob.objects.select_for_update().select_related(
            'driver_duty__driver','request').filter(pk=job_id).first()
        if job is None:reject(404,'JOB_NOT_FOUND','Job not found')
        _authorization(job,actor)
        digest=stable_digest({'job_id':str(job.pk),'actor_id':str(actor.pk),
              'latitude':str(lat),'longitude':str(lon),
              'accuracy_m':accuracy_m,'observed_at':observed_at.isoformat(),
              'sequence':sequence,'client_event_id':str(client_id)})
        old=CoWLocationObservation.objects.filter(job=job,actor=actor,
                                                   client_event_id=client_id).first()
        if old is not None:
            if old.payload_sha256!=digest:
                reject(409,'LOCATION_REPLAY_CONFLICT','Event id reused with changed observation')
            return {**_location_public(old,show_coordinates=True),'replayed':True}
        _time(observed_at,now)
        previous=CoWLocationObservation.objects.filter(job=job,actor=actor).order_by('-sequence').first()
        if previous is not None:
            delta=(observed_at-previous.observed_at).total_seconds()
            if sequence<=previous.sequence or delta<1:
                reject(409,'LOCATION_SEQUENCE_OR_CLOCK_REGRESSION','Location observations must advance monotonically')
            metres=geodesic_m(previous.latitude,previous.longitude,lat,lon)
            # Anomalous displacement is rejected, with accuracy tolerance.
            if metres>max(0,delta*70)+previous.accuracy_m+accuracy_m:
                reject(409,'LOCATION_IMPLAUSIBLE_DISPLACEMENT','Movement exceeds configured bounds')
        row=CoWLocationObservation.objects.create(
            job=job,actor=actor,client_event_id=client_id,
            sequence=sequence,latitude=lat,longitude=lon,
            accuracy_m=accuracy_m,observed_at=observed_at,payload_sha256=digest)
        return {**_location_public(row,show_coordinates=True),'replayed':False}


def is_fresh_driver_sample(row, now):
    """Same accuracy/clock criterion for public tracking and arrival proof."""
    return (row is not None and
            now-row.observed_at<=timedelta(seconds=getattr(settings,'COW_LOCATION_FRESH_SECONDS',90)) and
            row.observed_at<=now+timedelta(seconds=10) and
            row.accuracy_m<=getattr(settings,'COW_LOCATION_MAX_ACCURACY_M',50))


def latest_driver(job,*,now=None,require_fresh=False):
    now=now or timezone.now()
    duty=job.driver_duty
    if (not getattr(settings,'COW_LOCATION_RECEIVE_ENABLED',False) or
        duty is None or duty.ended_at is not None or duty.state!='AVAILABLE' or
        duty.driver.eligibility!='ACTIVE' or duty.driver.principal.status!='ACTIVE'):
        if require_fresh:
            reject(409,'LOCATION_DRIVER_NOT_ACTIVE','Current eligible location-enabled duty required')
        return None
    who=duty.driver.principal_id
    row=CoWLocationObservation.objects.filter(job=job,actor_id=who).order_by('-sequence').first()
    if row is None:
        if require_fresh:reject(409,'LOCATION_DRIVER_OBSERVATION_MISSING','Driver location not available')
        return None
    if require_fresh and not is_fresh_driver_sample(row,now):
        reject(409,'LOCATION_DRIVER_STALE_OR_INACCURATE','Fresh accurate driver location required')
    return row


def _address(job):
    addr=job.request.address_snapshot
    if not isinstance(addr,dict):
        reject(409,'LOCATION_ADDRESS_NOT_FROZEN','Frozen customer address unavailable')
    try:
        return (decimal_coordinate(addr['latitude'],'latitude'),
                decimal_coordinate(addr['longitude'],'longitude'))
    except (GeometryError,KeyError,ValueError,TypeError):
        reject(409,'LOCATION_ADDRESS_INVALID','Frozen customer address has invalid coordinates')


def assert_driver_near(job,now=None):
    now=now or timezone.now()
    row=latest_driver(job,now=now,require_fresh=True)
    target_lat,target_lon=_address(job)
    verdict=classify_radius(row.latitude,row.longitude,target_lat,target_lon,
       getattr(settings,'COW_ARRIVAL_RADIUS_M',100),row.accuracy_m)
    if verdict.state!='INSIDE':
        reject(409,'LOCATION_ARRIVAL_NOT_PROVEN','Driver must be definitively within allowed radius')
    # A recent customer observation, if present, must not contradict the frozen address.
    customer_row=CoWLocationObservation.objects.filter(
        job=job,actor_id=job.request.customer_id).order_by('-sequence').first()
    if customer_row is not None and now-customer_row.observed_at<=timedelta(seconds=90):
        cv=classify_radius(customer_row.latitude,customer_row.longitude,target_lat,target_lon,
            getattr(settings,'COW_ARRIVAL_RADIUS_M',100)*2,customer_row.accuracy_m)
        if cv.state=='OUTSIDE':
            reject(409,'LOCATION_CUSTOMER_ADDRESS_CONTRADICTION','Customer location conflicts with job address')
    return {'observation_id':str(row.pk),'distance_m':verdict.distance_m,
            'accuracy_m':row.accuracy_m,'radius_m':verdict.radius_m,
            'observed_at':row.observed_at.isoformat(),'method':verdict.method,
            'device_reported':True}


def tracking_for_customer(owner,job_id):
    if getattr(owner,'plane',None)!='app' or owner.status!='ACTIVE':
        reject(403,'TRACKING_CUSTOMER_REQUIRED','Active customer required')
    job=ServiceJob.objects.select_related('request','driver_duty__driver').filter(
        pk=job_id,request__customer=owner).first()
    if job is None:reject(404,'JOB_NOT_FOUND','Job not found')
    if job.state not in TRACKABLE:
        return {'job_id':str(job.pk),'job_state':job.state,'tracking_state':'NOT_ACTIVE',
                'latest':None,'next_poll_seconds':None,'event_schema':'cow.driver-location.v1'}
    now=timezone.now()
    row=latest_driver(job,now=now)
    if row is None:
        return {'job_id':str(job.pk),'job_state':job.state,'tracking_state':'NO_SIGNAL',
                'latest':None,'next_poll_seconds':5,'event_schema':'cow.driver-location.v1'}
    stale=not is_fresh_driver_sample(row,now)
    return {'job_id':str(job.pk),'job_state':job.state,'tracking_state':'STALE' if stale else 'LIVE',
           'latest':_location_public(row,show_coordinates=True),
           'next_poll_seconds':5 if stale else 3,'event_schema':'cow.driver-location.v1',
           'routing_available':False,'road_eta_available':False}


def published_zone_radius(point_lat,point_lon,zone_id):
    """Choose the Zone on the SERVER, never by the customer's price preference.

    Deterministic policy for overlapping published Zones:
    1. Covering Zones: smallest radius (most specific), UUID tie break.
    2. Otherwise: nearest ADVISORY Zone by outside distance, UUID tie break.
    STRICT Zones cannot admit a destination outside their own radius.

    The caller-supplied zone_id only CONFIRMS this selection. It cannot select
    a different, potentially cheaper, published tariff. All financial service
    eligibility and assigned tariff/GST are independently checked downstream.
    """
    try:
        latitude=decimal_coordinate(point_lat,'latitude')
        longitude=decimal_coordinate(point_lon,'longitude')
    except GeometryError as exc:
        reject(422,'LOCATION_ZONE_POINT_INVALID',exc.code)
    now=timezone.now()
    candidates=[]
    for zone in ServiceZone.objects.filter(status='PUBLISHED').order_by('pk').iterator():
        if (zone.starts_at is not None and zone.starts_at>now) or (zone.ends_at is not None and zone.ends_at<=now):
            continue
        distance=geodesic_m(zone.center_latitude,zone.center_longitude,latitude,longitude)
        inside=distance<=zone.radius_m
        if inside:
            candidates.append(((0,zone.radius_m,str(zone.pk)),zone,'INSIDE',distance))
        elif zone.restriction_mode=='ADVISORY':
            candidates.append(((1,distance-zone.radius_m,str(zone.pk)),zone,'OUTSIDE',distance))
    if not candidates:
        reject(409,'LOCATION_NO_SERVICEABLE_ZONE','No published applicable service Zone')
    _rank,zone,relation,distance=min(candidates,key=lambda row:row[0])
    if zone_id is not None and str(zone.pk)!=str(zone_id):
        reject(409,'LOCATION_ZONE_NONCANONICAL',
            'Requested Zone does not match the server-selected applicable Zone')
    return zone,relation,distance
