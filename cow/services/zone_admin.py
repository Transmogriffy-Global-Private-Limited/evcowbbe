"""Sprint 7: Admin-owned Zone commercial configuration.

One transaction/authorization boundary per mutation. The existing PricingPolicy
is the only tariff publication ledger; ZoneTariffPayload is its immutable
composite payload. No customer commitment, wallet debit, or HAL command here.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, connection, transaction
from django.utils import timezone as dj_timezone

from cow.common.errors import reject
from cow.domain.zone_position import VerifiedPosition, radial_relation
from cow.domain.zone_tariff_convergence import (
    CommercialContractError, Evidence, TaxBindingUnavailable, TrustedCommercialContext,
    ZoneGSTBinding, validate_tariff_plan,
)
from cow.models import (
    AuditEvent, Charger, ChargerZoneAssignment, DutySession,
    DutyZoneAssignment, PricingPolicy, ServiceZone, ZoneGSTAssignment,
    ZoneTariffPayload,
)
from cow.services import pricing_admin, rbac, zone_commercial_registry
from cow.services.admin_rbac import _lock_global, _reason, _require_locked_actor
from cow.services.pricing_topology import validate_topology
from cow.services.pricing_engine import PricingError

CHANNELS = frozenset({'ROAMING', 'ON_SITE_QR'})


def _at(value, label='timestamp'):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        reject(422, 'INVALID_WINDOW', f'Timezone-aware {label} required')
    return value.astimezone(timezone.utc)


def _window(start, end):
    if start is not None:
        start = _at(start, 'starts_at')
    if end is not None:
        end = _at(end, 'ends_at')
    if end is not None and (start is None or end <= start):
        reject(422, 'INVALID_WINDOW', 'ends_at requires an earlier starts_at')
    return start, end


def _audit(actor, target, action, reason, **details):
    AuditEvent.objects.create(actor=actor, target_type=type(target).__name__,
                              target_id=target.pk, action=action,
                              details={'reason': _reason(reason), **details})


def _lock(label):
    value = int.from_bytes(hashlib.sha256(('cow-zone-admin-v1:' + label).encode()).digest()[:8],
                           'big', signed=True)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [value])


def _actor(principal, capability='pricing.manage', extra=None):
    _lock_global()
    actor = _require_locked_actor(principal, capability)
    if extra:
        rbac.require(actor, extra)
    return actor


def _get_zone(zone_id, *, lock=False):
    qs = ServiceZone.objects.select_for_update() if lock else ServiceZone.objects
    zone = qs.filter(pk=zone_id).first()
    if zone is None:
        reject(404, 'ZONE_NOT_FOUND', 'Zone revision not found')
    return zone


def _effective(zone, instant):
    if zone.status != 'PUBLISHED' or (zone.starts_at and instant < zone.starts_at) or (zone.ends_at and instant >= zone.ends_at):
        reject(409, 'ZONE_NOT_EFFECTIVE', 'Published, effective Zone revision required')


def _scope(channel, zone):
    if channel not in CHANNELS:
        reject(422, 'INVALID_CHANNEL', 'Unknown charging channel')
    return {'kind': 'ZONE_' + channel, 'key': zone.zone_key}


def zone_view(zone):
    return {'id': str(zone.pk), 'zone_key': zone.zone_key, 'version': zone.version,
            'name': zone.name, 'center_latitude': str(zone.center_latitude),
            'center_longitude': str(zone.center_longitude), 'radius_m': zone.radius_m,
            'restriction_mode': zone.restriction_mode, 'status': zone.status,
            'starts_at': zone.starts_at.isoformat() if zone.starts_at else None,
            'ends_at': zone.ends_at.isoformat() if zone.ends_at else None,
            'created_by_id': str(zone.created_by_id)}


def assignment_view(obj, kind):
    return {'id': str(obj.pk), 'kind': kind, 'zone_id': str(obj.zone_id),
            'owner_id': str(obj.duty_id if kind == 'duty' else obj.charger_id),
            'starts_at': obj.starts_at.isoformat(),
            'ends_at': obj.ends_at.isoformat() if obj.ends_at else None,
            'authorization_ref': obj.authorization_ref}


def gst_view(obj):
    return {'id': str(obj.pk), 'zone_id': str(obj.zone_id), 'channel': obj.channel,
            'binding_version': obj.binding_version, 'status': obj.status,
            'starts_at': obj.starts_at.isoformat(), 'ends_at': obj.ends_at.isoformat() if obj.ends_at else None,
            'seller_tax_ref': obj.seller_tax_ref, 'legal_decision_ref': obj.legal_decision_ref,
            'tax_profiles': obj.tax_profiles, 'authorization_ref': obj.authorization_ref}


def tariff_view(policy):
    return {'policy': pricing_admin.encode(policy),
            'zone_id': str(policy.zone_payload.zone_id) if hasattr(policy, 'zone_payload') else None,
            'channel': policy.zone_payload.channel if hasattr(policy, 'zone_payload') else None,
            'plan': policy.zone_payload.plan if hasattr(policy, 'zone_payload') else None}


def list_zones(actor, status='', limit=50):
    rbac.require(actor, 'pricing.read')
    qs = ServiceZone.objects.all().order_by('zone_key', '-version')
    if status:
        if status not in {'DRAFT', 'PUBLISHED', 'RETIRED'}:
            reject(422, 'INVALID_ZONE_FILTER', 'Unknown Zone status')
        qs = qs.filter(status=status)
    return [zone_view(x) for x in qs[:limit]]


def get_zone(actor, zone_id):
    rbac.require(actor, 'pricing.read')
    return zone_view(_get_zone(zone_id))


def create_zone(actor, data):
    reason = _reason(data['reason'])
    start, end = _window(data.get('starts_at'), data.get('ends_at'))
    key = data['zone_key']
    if not key or len(key) > 160:
        reject(422, 'INVALID_ZONE_KEY', 'Invalid Zone key')
    try:
        import re
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', key):
            reject(422, 'INVALID_ZONE_KEY', 'Invalid Zone key')
        lat = Decimal(str(data['center_latitude']))
        lon = Decimal(str(data['center_longitude']))
        if not lat.is_finite() or not lon.is_finite() or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            reject(422, 'INVALID_ZONE_POSITION', 'Zone coordinates outside WGS84')
        if lat.as_tuple().exponent < -7 or lon.as_tuple().exponent < -7:
            reject(422, 'INVALID_ZONE_POSITION', 'Coordinates exceed database precision')
    except (ValueError, ArithmeticError):
        reject(422, 'INVALID_ZONE_POSITION', 'Invalid Zone position')
    with transaction.atomic():
        actor = _actor(actor)
        _lock('zone-key:' + key)
        top = ServiceZone.objects.filter(zone_key=key).order_by('-version').first()
        expected = (top.version + 1) if top else 1
        if data['version'] != expected:
            reject(409, 'ZONE_REVISION_CONFLICT', 'Zone version must be next sequential revision')
        try:
            zone = ServiceZone.objects.create(
                zone_key=key, version=data['version'], name=data['name'],
                center_latitude=lat, center_longitude=lon, radius_m=data['radius_m'],
                restriction_mode=data['restriction_mode'], status='DRAFT',
                starts_at=start, ends_at=end, created_by=actor)
        except IntegrityError:
            reject(409, 'ZONE_REVISION_CONFLICT', 'Zone creation violates database constraints')
        _audit(actor, zone, 'ZONE_DRAFT_CREATED', reason, zone_key=key, version=zone.version)
    return zone_view(zone)


def transition_zone(actor, zone_id, action, reason):
    if action not in {'publish', 'retire'}:
        reject(400, 'INVALID_ZONE_TRANSITION', 'Invalid Zone transition')
    with transaction.atomic():
        actor = _actor(actor)
        zone_key = ServiceZone.objects.filter(pk=zone_id).values_list('zone_key', flat=True).first()
        if zone_key is None:
            reject(404, 'ZONE_NOT_FOUND', 'Zone revision not found')
        _lock('zone-key:' + zone_key)
        zone = _get_zone(zone_id, lock=True)
        if action == 'publish':
            if zone.status != 'DRAFT':
                reject(409, 'ZONE_STATE_CONFLICT', 'Only draft Zones can be published')
            if ServiceZone.objects.filter(zone_key=zone.zone_key, status='PUBLISHED').exclude(pk=zone.pk).exists():
                reject(409, 'ZONE_ACTIVE_REVISION', 'Retire previous published revision first')
            if zone.ends_at and zone.ends_at <= dj_timezone.now():
                reject(409, 'ZONE_WINDOW_EXPIRED', 'Cannot publish an expired Zone')
            zone.status = 'PUBLISHED'
        else:
            if zone.status != 'PUBLISHED':
                reject(409, 'ZONE_STATE_CONFLICT', 'Only published Zones can be retired')
            instant = dj_timezone.now()
            from django.db.models import Q
            if (DutyZoneAssignment.objects.filter(zone=zone)
                    .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=instant)).exists() or
                    ChargerZoneAssignment.objects.filter(zone=zone)
                    .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=instant)).exists()):
                reject(409, 'ZONE_ASSIGNMENTS_ACTIVE', 'Close live/future Zone assignments before retirement')
            if ZoneTariffPayload.objects.filter(zone=zone, policy__status='PUBLISHED').exists():
                reject(409, 'ZONE_TARIFF_ACTIVE', 'Withdraw published Zone tariffs before retirement')
            if ZoneGSTAssignment.objects.filter(zone=zone, status='APPROVED').filter(
                    Q(ends_at__isnull=True) | Q(ends_at__gt=instant)).exists():
                reject(409, 'ZONE_GST_ACTIVE', 'Close approved GST bindings before retirement')
            zone.status = 'RETIRED'
        zone.save(update_fields=['status'])
        _audit(actor, zone, 'ZONE_' + action.upper(), reason, zone_key=zone.zone_key,
               version=zone.version)
    return zone_view(zone)


def _assignment_model(kind):
    if kind == 'duty':
        return DutyZoneAssignment, DutySession, 'duty'
    if kind == 'charger':
        return ChargerZoneAssignment, Charger, 'charger'
    reject(422, 'INVALID_ASSIGNMENT_KIND', 'Expected duty or charger')


def _assign_permission(kind):
    return 'dispatch.manage' if kind == 'duty' else 'fleet.assign'


def assign_zone(actor, kind, data):
    Model, Owner, field = _assignment_model(kind)
    start, end = _window(data['starts_at'], data.get('ends_at'))
    if start is None:
        reject(422, 'INVALID_WINDOW', 'Assignment start required')
    reason = _reason(data['reason'])
    with transaction.atomic():
        actor = _actor(actor, _assign_permission(kind))
        zone = _get_zone(data['zone_id'], lock=True)
        _effective(zone, start)
        if end is not None and zone.ends_at is not None and end > zone.ends_at:
            reject(409, 'ZONE_WINDOW_CONFLICT', 'Assignment extends beyond Zone effective window')
        if end is None and zone.ends_at is not None:
            reject(409, 'ZONE_WINDOW_CONFLICT', 'Bounded Zone needs bounded assignment')
        owner = Owner.objects.select_for_update().filter(pk=data['owner_id']).first()
        if owner is None:
            reject(404, 'ASSIGNMENT_OWNER_NOT_FOUND', 'Target duty/charger not found')
        if kind == 'duty':
            if start < owner.started_at or (owner.ended_at and (end is None or end > owner.ended_at)):
                reject(409, 'DUTY_TIME_CONFLICT', 'Zone assignment outside duty session')
        _lock('assignment:' + kind + ':' + str(owner.pk))
        try:
            assignment = Model.objects.create(
                **{field: owner}, zone=zone, starts_at=start, ends_at=end,
                authorized_by=actor, authorization_ref=data['authorization_ref'])
        except IntegrityError:
            reject(409, 'ASSIGNMENT_CONFLICT', 'Conflicting Zone assignment')
        _audit(actor, assignment, 'ZONE_ASSIGNMENT_CREATED', reason, kind=kind,
               owner_id=str(owner.pk), zone_id=str(zone.pk))
    return assignment_view(assignment, kind)


def close_assignment(actor, kind, assignment_id, reason):
    Model, _Owner, _field = _assignment_model(kind)
    with transaction.atomic():
        actor = _actor(actor, _assign_permission(kind))
        obj = Model.objects.select_for_update().filter(pk=assignment_id).first()
        if obj is None:
            reject(404, 'ASSIGNMENT_NOT_FOUND', 'Zone assignment not found')
        _lock('assignment:' + kind + ':' + str(obj.duty_id if kind == 'duty' else obj.charger_id))
        if obj.ends_at is not None:
            reject(409, 'ASSIGNMENT_STATE_CONFLICT', 'Assignment already closed')
        # Keep both the effective-time predicate and new value in the database's
        # statement clock. Python clock skew must never cause a spurious failure.
        with connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE {connection.ops.quote_name(Model._meta.db_table)} '
                'SET ends_at=statement_timestamp() '
                'WHERE id=%s AND ends_at IS NULL AND starts_at < statement_timestamp() '
                'RETURNING ends_at',
                [obj.pk],
            )
            updated = cursor.fetchone()
        if updated is None:
            reject(409, 'ASSIGNMENT_STATE_CONFLICT', 'Assignment already closed')
        obj.ends_at = updated[0]
        _audit(actor, obj, 'ZONE_ASSIGNMENT_CLOSED', reason, kind=kind, ends_at=obj.ends_at.isoformat())
    return assignment_view(obj, kind)


def create_gst(actor, data):
    reason = _reason(data['reason'])
    start, end = _window(data['starts_at'], data.get('ends_at'))
    if start is None:
        reject(422, 'INVALID_WINDOW', 'GST decision start required')
    with transaction.atomic():
        actor = _actor(actor, 'pricing.manage', 'invoices.manage')
        zone = _get_zone(data['zone_id'], lock=True)
        _effective(zone, start)
        if zone.ends_at is not None and (end is None or end > zone.ends_at):
            reject(409, 'ZONE_WINDOW_CONFLICT', 'GST window outside Zone lifetime')
        try:
            validated = ZoneGSTBinding(zone_id=zone.zone_key, channel=data['channel'],
                binding_version=data['binding_version'], starts_at=start, ends_at=end,
                seller_tax_ref=data['seller_tax_ref'], legal_decision_ref=data['legal_decision_ref'],
                tax_profiles=data['tax_profiles'])
        except (CommercialContractError, TaxBindingUnavailable) as exc:
            reject(422, 'INVALID_TAX_DECISION', str(exc))
        _lock('zone-gst:' + str(zone.pk) + ':' + validated.channel)
        highest = ZoneGSTAssignment.objects.filter(zone=zone, channel=validated.channel).order_by('-binding_version').first()
        if validated.binding_version != (highest.binding_version + 1 if highest else 1):
            reject(409, 'GST_VERSION_CONFLICT', 'GST binding version must advance sequentially')
        try:
            obj = ZoneGSTAssignment.objects.create(
                zone=zone, channel=validated.channel, binding_version=validated.binding_version,
                starts_at=start, ends_at=end, seller_tax_ref=validated.seller_tax_ref,
                legal_decision_ref=validated.legal_decision_ref, tax_profiles=data['tax_profiles'],
                authorized_by=actor, authorization_ref=data['authorization_ref'], status='DRAFT')
        except IntegrityError:
            reject(409, 'GST_CONFLICT', 'Invalid GST revision')
        _audit(actor, obj, 'GST_DRAFT_RECORDED', reason, zone_id=str(zone.pk),
               legal_decision_ref=obj.legal_decision_ref)
    return gst_view(obj)


def approve_gst(actor, gst_id, reason):
    with transaction.atomic():
        actor = _actor(actor, 'pricing.manage', 'invoices.manage')
        obj = ZoneGSTAssignment.objects.select_for_update().filter(pk=gst_id).first()
        if obj is None:
            reject(404, 'GST_NOT_FOUND', 'GST decision not found')
        if obj.status != 'DRAFT':
            reject(409, 'GST_STATE_CONFLICT', 'Only draft tax decisions can be approved')
        zone = _get_zone(obj.zone_id, lock=True)
        _effective(zone, obj.starts_at)
        if zone.ends_at is not None and (obj.ends_at is None or obj.ends_at > zone.ends_at):
            reject(409, 'ZONE_WINDOW_CONFLICT', 'GST window exceeds published Zone lifetime')
        obj.status = 'APPROVED'
        try:
            obj.save(update_fields=['status'])
        except IntegrityError:
            reject(409, 'GST_TIME_CONFLICT', 'An approved GST binding overlaps this decision')
        _audit(actor, obj, 'GST_APPROVAL_RECORDED', reason,
               legal_decision_ref=obj.legal_decision_ref,
               authorization_ref=obj.authorization_ref,
               note='Recorded operator decision, not independent legal verification')
    return gst_view(obj)


def close_gst(actor, gst_id, reason):
    with transaction.atomic():
        actor = _actor(actor, 'pricing.manage', 'invoices.manage')
        obj = ZoneGSTAssignment.objects.select_for_update().filter(pk=gst_id).first()
        if obj is None:
            reject(404, 'GST_NOT_FOUND', 'GST decision not found')
        if obj.status != 'APPROVED' or obj.ends_at is not None:
            reject(409, 'GST_STATE_CONFLICT', 'Only open approved binding may be closed')
        # A published policy must not silently lose its approved tax coverage.
        from django.db.models import Q
        if ZoneTariffPayload.objects.filter(zone=obj.zone, channel=obj.channel,
                policy__status='PUBLISHED').filter(
                Q(policy__ends_at__isnull=True) | Q(policy__ends_at__gt=dj_timezone.now())
        ).exists():
            reject(409, 'GST_TARIFF_ACTIVE', 'Withdraw active Zone tariffs before closing tax coverage')
        # PostgreSQL guard compares against statement_timestamp(): use that
        # same statement clock for both the predicate and the closing value.
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE cow_zonegstassignment SET ends_at=statement_timestamp() '
                "WHERE id=%s AND status='APPROVED' AND ends_at IS NULL "
                'AND starts_at < statement_timestamp() RETURNING ends_at',
                [obj.pk],
            )
            updated = cursor.fetchone()
        if updated is None:
            reject(409, 'GST_STATE_CONFLICT', 'GST decision already closed')
        obj.ends_at = updated[0]
        _audit(actor, obj, 'GST_BINDING_CLOSED', reason, ends_at=obj.ends_at.isoformat())
    return gst_view(obj)


def _tax_classes(plan):
    classes = set()
    for profile in plan['profiles'].values():
        for comp in ('charging', 'transport', 'distance'):
            classes.add(profile[comp]['tax_class'])
        classes.update(item['tax_class'] for item in profile['extras'])
    return classes


def create_tariff(actor, data):
    reason = _reason(data['reason'])
    start, end = _window(data.get('starts_at'), data.get('ends_at'))
    plan = data['plan']
    try:
        validate_tariff_plan(plan)
    except CommercialContractError as exc:
        reject(422, 'INVALID_ZONE_TARIFF', str(exc))
    with transaction.atomic():
        actor = _actor(actor)
        zone = _get_zone(data['zone_id'], lock=True)
        _effective(zone, start or dj_timezone.now())
        if plan['zone_id'] != zone.zone_key:
            reject(422, 'ZONE_TARIFF_MISMATCH', 'Plan does not belong to selected Zone')
        scope = _scope(plan['channel'], zone)
        _lock('zone-policy:' + plan['policy_id'])
        latest = PricingPolicy.objects.filter(policy_key=plan['policy_id']).order_by('-policy_version').first()
        if plan['policy_version'] != (latest.policy_version + 1 if latest else 1):
            reject(409, 'POLICY_VERSION_CONFLICT', 'Zone pricing versions must advance sequentially')
        try:
            # Empty rules deliberately distinguish composite Zone tariffs from
            # generic pricing. Migration 0009 enforces this distinction in SQL.
            policy = PricingPolicy.objects.create(
                name=plan['policy_id'], revision=plan['policy_version'], status='DRAFT',
                policy_key=plan['policy_id'], policy_version=plan['policy_version'],
                scope=scope, rules=[], currency='INR', state_version=1,
                starts_at=start, ends_at=end, created_by=actor)
            ZoneTariffPayload.objects.create(
                policy=policy, zone=zone, channel=plan['channel'], plan=plan,
                authorized_by=actor)
        except IntegrityError:
            reject(409, 'POLICY_VERSION_CONFLICT', 'Zone tariff revision conflicts with existing policy')
        _audit(actor, policy, 'ZONE_TARIFF_DRAFT_CREATED', reason,
               zone_id=str(zone.pk), channel=plan['channel'])
    return tariff_view(policy)


def validate_zone_publication(policy, published):
    """Called by pricing_admin.publish inside the same RBAC/scope/row lock."""
    if policy.scope.get('kind') not in {'ZONE_ROAMING', 'ZONE_ON_SITE_QR'}:
        reject(422, 'INVALID_ZONE_SCOPE', 'Unsupported Zone pricing kind')
    payload = ZoneTariffPayload.objects.select_related('zone').filter(policy=policy).first()
    if payload is None:
        reject(409, 'ZONE_PAYLOAD_MISSING', 'Zone publication requires composite payload')
    zone = payload.zone
    _effective(zone, policy.starts_at or dj_timezone.now())
    if zone.ends_at is not None and (policy.ends_at is None or policy.ends_at > zone.ends_at):
        reject(409, 'ZONE_TARIFF_WINDOW', 'Tariff exceeds Zone lifetime')
    if payload.channel != policy.scope['kind'].removeprefix('ZONE_') or policy.scope.get('key') != zone.zone_key:
        reject(409, 'ZONE_TARIFF_MISMATCH', 'Zone tariff scope differs from payload')
    plan = payload.plan
    try:
        validate_tariff_plan(plan)
    except CommercialContractError as exc:
        reject(422, 'INVALID_ZONE_TARIFF', str(exc))
    if plan['policy_id'] != policy.policy_key or plan['policy_version'] != policy.policy_version or plan['channel'] != payload.channel or plan['zone_id'] != zone.zone_key:
        reject(409, 'ZONE_TARIFF_MISMATCH', 'Versioned tariff identity mismatch')
    moment = policy.starts_at or dj_timezone.now()
    candidates = list(ZoneGSTAssignment.objects.filter(zone=zone, channel=payload.channel, status='APPROVED'))
    from cow.domain.zone_tariff_convergence import select_zone_gst
    bindings = [ZoneGSTBinding(zone_id=zone.zone_key, channel=x.channel,
                binding_version=x.binding_version, starts_at=x.starts_at, ends_at=x.ends_at,
                seller_tax_ref=x.seller_tax_ref, legal_decision_ref=x.legal_decision_ref,
                tax_profiles=x.tax_profiles) for x in candidates]
    try:
        selected = select_zone_gst(bindings, zone.zone_key, payload.channel, moment)
    except TaxBindingUnavailable as exc:
        reject(409, 'GST_NOT_READY', str(exc))
    if _tax_classes(plan) - set(selected.tax_profiles):
        reject(409, 'GST_TAX_CLASS_MISSING', 'Approved GST decision lacks tariff tax classes')
    # The general topology algorithm applies to the same publication ledger,
    # but generic rule shapes must never be substituted for composite tariffs.
    synthetic = lambda x: {'scope': x.scope, 'starts_at': x.starts_at,
        'ends_at': x.ends_at, 'active': True,
        'terms': {'policy_id': x.policy_key, 'policy_version': x.policy_version,
                  'currency': 'INR', 'rules': [{'id': 'ZONE_ONLY', 'kind':'BASE',
                  'component':'ZONE_ONLY', 'basis':'ZONE_ONLY', 'tax_class':'CHARGING',
                  'rate':'0'}]}}
    try:
        validate_topology([synthetic(x) for x in published] + [synthetic(policy)])
    except PricingError:
        reject(409, 'ZONE_TARIFF_WINDOW', 'Conflicting published Zone tariff windows')


def get_tariff(actor, policy_id):
    rbac.require(actor, 'pricing.read')
    policy = PricingPolicy.objects.filter(pk=policy_id,
        scope__kind__in=('ZONE_ROAMING', 'ZONE_ON_SITE_QR')).first()
    if policy is None:
        reject(404, 'ZONE_TARIFF_NOT_FOUND', 'Zone tariff not found')
    return tariff_view(policy)


def list_gst(actor, zone_id):
    rbac.require(actor, 'pricing.read')
    _get_zone(zone_id)
    return [gst_view(x) for x in ZoneGSTAssignment.objects.filter(zone_id=zone_id).order_by('channel', 'binding_version')]


def list_assignments(actor, zone_id):
    rbac.require(actor, 'pricing.read')
    _get_zone(zone_id)
    return ([assignment_view(x, 'duty') for x in DutyZoneAssignment.objects.filter(zone_id=zone_id)] +
            [assignment_view(x, 'charger') for x in ChargerZoneAssignment.objects.filter(zone_id=zone_id)])


def list_tariffs(actor, zone_id):
    rbac.require(actor, 'pricing.read')
    _get_zone(zone_id)
    return [tariff_view(x.policy) for x in ZoneTariffPayload.objects.select_related('policy')
            .filter(zone_id=zone_id).order_by('-created_at')]


def preview_scenario(actor, data):
    """Admin simulation only: user-entered position is NOT trusted live evidence."""
    rbac.require(actor, 'pricing.read')
    moment = _at(data.get('at') or dj_timezone.now())
    channel = data['channel']
    if channel not in CHANNELS:
        reject(422, 'INVALID_CHANNEL', 'Invalid charging channel')
    try:
        position = VerifiedPosition(Decimal(data['latitude']), Decimal(data['longitude']),
                                    Decimal(data['accuracy_m']), moment, 'ADMIN_SCENARIO_UNVERIFIED')
    except (ValueError, ArithmeticError) as exc:
        reject(422, 'INVALID_SCENARIO_POSITION', str(exc))
    try:
        if channel == 'ROAMING':
            if not data.get('duty_id') or data.get('charger_id'):
                reject(422, 'INVALID_SCENARIO', 'Roaming requires only a duty ID')
            zone, assignment = zone_commercial_registry.assigned_roaming_zone(data['duty_id'], moment)
            duty_id, site_id = str(assignment.duty_id), None
        else:
            if not data.get('charger_id') or data.get('duty_id'):
                reject(422, 'INVALID_SCENARIO', 'QR requires only a charger ID')
            zone, assignment = zone_commercial_registry.assigned_qr_zone(data['charger_id'], moment)
            duty_id, site_id = None, str(assignment.charger_id)
        relation = radial_relation(zone.center_latitude, zone.center_longitude, zone.radius_m, position)
        if relation == 'UNKNOWN' or (relation == 'OUTSIDE' and
                (zone.restriction_mode == 'STRICT' or channel == 'ON_SITE_QR')):
            reject(409, 'SCENARIO_NOT_SERVICEABLE', 'Location is outside permitted Zone or uncertain')
        context = TrustedCommercialContext(zone_id=zone.zone_key, channel=channel,
            relation=relation, decision_at=moment,
            provenance='ADMIN_SCENARIO_ONLY_NOT_VERIFIED_NOT_COMMITTABLE',
            driver_duty_id=duty_id, site_id=site_id,
            outside_authorized=(relation == 'OUTSIDE' and zone.restriction_mode == 'ADVISORY'))
        result = zone_commercial_registry.preview_upfront(zone, context, Evidence())
    except (CommercialContractError, ValueError) as exc:
        reject(409, 'SCENARIO_UNAVAILABLE', str(exc))
    return {'simulation_only': True, 'may_charge_customer': False,
            'may_accept_quote': False, 'may_command_hal': False,
            'position_trusted': False, 'zone': zone_view(zone), 'preview': result}
