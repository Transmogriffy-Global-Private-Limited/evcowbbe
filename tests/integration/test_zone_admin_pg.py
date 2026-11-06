"""Sprint 7 DB acceptance, under the repository's marked disposable PG harness."""
import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from cow.common.errors import DomainError
from cow.domain.zone_tariff_convergence import Evidence, TrustedCommercialContext
from cow.models import (Account, AdminRole, AuditEvent, Charger, Principal, PricingPolicy,
                        ServiceZone, ZoneGSTAssignment, ZoneTariffPayload)
from cow.services import auth, pricing_admin, zone_admin, zone_commercial_registry

pytestmark = [pytest.mark.postgres, pytest.mark.django_db]


@pytest.fixture
def operator():
    account = Account.objects.create(email=f's7-{uuid4().hex}@example.invalid',
                                     password_hash='not-a-login', email_verified=True)
    principal = Principal.objects.create(account=account, plane='admin', status='ACTIVE')
    AdminRole.objects.create(principal=principal, role='FULL_ADMIN')
    return principal


def zone_fixture(actor):
    start = timezone.now() - timedelta(hours=1)
    key = 'S7_' + uuid4().hex[:12].upper()
    value = zone_admin.create_zone(actor, {
        'zone_key': key, 'version': 1, 'name': 'Acceptance Zone',
        'center_latitude': '22.5000000', 'center_longitude': '88.3000000',
        'radius_m': 5000, 'restriction_mode': 'STRICT',
        'starts_at': start, 'ends_at': None, 'reason': 'Reviewed acceptance configuration'})
    assert value['status'] == 'DRAFT'
    zone_admin.transition_zone(actor, UUID(value['id']), 'publish', 'Approved zone for test')
    return ServiceZone.objects.get(pk=value['id']), start


def make_plan(zone, channel):
    return {'policy_id': 'S7_TARIFF_' + uuid4().hex[:12].upper(), 'policy_version': 1,
        'zone_id': zone.zone_key, 'channel': channel, 'currency': 'INR',
        'profiles': {'INSIDE': {
            'charging': {'basis': 'ENERGY_KWH', 'rate': '10.00', 'tax_class': 'ENERGY'},
            'transport': {'rate': '100.00' if channel == 'ROAMING' else '0', 'tax_class': 'MOBILITY',
                          'applies_on': ['UPFRONT']},
            'distance': {'minimum_billable_m': 2000 if channel == 'ROAMING' else 0,
                         'tax_class': 'MOBILITY',
                         'applies_on': ['UPFRONT'],
                         'slabs': [{'up_to_m': None, 'rate_per_km': '5.00' if channel == 'ROAMING' else '0'}]},
            'extras': ([{'id': 'BOOKING_FEE', 'basis': 'FIXED', 'rate': '10.00',
                        'tax_class': 'MOBILITY', 'applies_on': ['UPFRONT']}]
                       if channel == 'ON_SITE_QR' else []),
        }}}


def gst_fixture(actor, zone, start, channel):
    tax = {key: {'approved': True, 'sgst_percent': '0', 'cgst_percent': '0',
                 'igst_percent': '0'} for key in ('ENERGY', 'MOBILITY')}
    draft = zone_admin.create_gst(actor, {'zone_id': zone.pk,
        'channel': channel, 'binding_version': 1, 'starts_at': start,
        'ends_at': None, 'seller_tax_ref': 'SELLER:TEST',
        'legal_decision_ref': 'LEGAL:FIXTURE', 'tax_profiles': tax,
        'authorization_ref': 'REVIEW:FIXTURE', 'reason': 'Reviewed tax configuration'})
    approved = zone_admin.approve_gst(actor, UUID(draft['id']), 'Record approved legal decision')
    assert approved['status'] == 'APPROVED'


def tariff_fixture(actor, zone, start, channel):
    draft = zone_admin.create_tariff(actor, {'zone_id': zone.pk,
        'starts_at': start, 'ends_at': None, 'plan': make_plan(zone, channel),
        'reason': 'Reviewed composite tariff'})
    policy = PricingPolicy.objects.get(pk=draft['policy']['id'])
    assert policy.rules == []
    published = pricing_admin.publish(actor, policy.pk, 1, 'Publish approved composite tariff')
    assert published.status == 'PUBLISHED'
    assert ZoneTariffPayload.objects.filter(policy=published, zone=zone).exists()
    return published


def test_zone_to_tax_to_tariff_to_upfront_preview(operator):
    zone, start = zone_fixture(operator)
    gst_fixture(operator, zone, start, 'ROAMING')
    policy = tariff_fixture(operator, zone, start, 'ROAMING')
    ctx = TrustedCommercialContext(zone.zone_key, 'ROAMING', 'INSIDE', timezone.now(),
                                   'ADMIN_ACCEPTANCE_TEST_NOT_CUSTOMER_AUTHORITY', str(uuid4()))
    result = zone_commercial_registry.preview_upfront(zone, ctx, Evidence())
    assert result['total_minor'] == 11000  # 100 INR transport + 2 km @ 5 INR/km
    assert result['policy_snapshot']['policy_id'] == policy.policy_key
    assert result['tax_binding']['legal_decision_ref'] == 'LEGAL:FIXTURE'


def test_db_refuses_composite_policy_without_payload(operator):
    zone, start = zone_fixture(operator)
    p = PricingPolicy.objects.create(name='MISSING_ZONE_PAYLOAD', revision=1,
        policy_key='MISSING_ZONE_PAYLOAD', policy_version=1, status='DRAFT',
        scope={'kind': 'ZONE_ROAMING', 'key': zone.zone_key}, rules=[],
        starts_at=start, created_by=operator, state_version=1)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            p.status = 'PUBLISHED'
            p.state_version = 2
            p.save(update_fields=['status', 'state_version', 'updated_at'])
    p.refresh_from_db()
    assert p.status == 'DRAFT'


def test_missing_approved_gst_blocks_publish(operator):
    zone, start = zone_fixture(operator)
    draft = zone_admin.create_tariff(operator, {'zone_id': zone.pk,
        'starts_at': start, 'ends_at': None, 'plan': make_plan(zone, 'ROAMING'),
        'reason': 'Draft without approved GST'})
    p = PricingPolicy.objects.get(pk=draft['policy']['id'])
    with pytest.raises(DomainError):
        pricing_admin.publish(operator, p.pk, 1, 'Attempt without approved tax')
    p.refresh_from_db()
    assert p.status == 'DRAFT'


def test_single_published_zone_revision(operator):
    zone, start = zone_fixture(operator)
    next_revision = zone_admin.create_zone(operator, {
        'zone_key': zone.zone_key, 'version': 2, 'name': 'Second revision',
        'center_latitude': '22.5000000', 'center_longitude': '88.3000000',
        'radius_m': 6000, 'restriction_mode': 'STRICT', 'starts_at': start,
        'ends_at': None, 'reason': 'Versioned geography update'})
    with pytest.raises(DomainError):
        zone_admin.transition_zone(operator, UUID(next_revision['id']), 'publish',
                                   'Must retire the previous one')
    zone_admin.transition_zone(operator, zone.pk, 'retire', 'Retire published revision')
    result = zone_admin.transition_zone(operator, UUID(next_revision['id']), 'publish',
                                        'Publish replacement revision')
    assert result['status'] == 'PUBLISHED'
    assert ServiceZone.objects.filter(zone_key=zone.zone_key, status='PUBLISHED').count() == 1


def test_qr_configuration_and_scenario_cannot_charge(operator):
    zone, start = zone_fixture(operator)
    gst_fixture(operator, zone, start, 'ON_SITE_QR')
    tariff_fixture(operator, zone, start, 'ON_SITE_QR')
    suffix = uuid4().hex[:12]
    charger = Charger.objects.create(asset_tag='s7-asset-' + suffix,
        hal_identity='s7-hal-' + suffix, manufacturer='Test', model='Fixture',
        rated_power_w=7400)
    zone_admin.assign_zone(operator, 'charger', {'zone_id': zone.pk,
        'owner_id': charger.pk, 'starts_at': start, 'ends_at': None,
        'authorization_ref': 'OPS:FIXTURE', 'reason': 'Review charger assignment'})
    result = zone_admin.preview_scenario(operator, {'channel': 'ON_SITE_QR',
        'charger_id': charger.pk, 'latitude': '22.5000000',
        'longitude': '88.3000000', 'accuracy_m': '1'})
    assert result['simulation_only'] and not result['position_trusted']
    assert not result['may_charge_customer'] and not result['may_accept_quote']
    assert result['preview']['total_minor'] == 1000  # 10 INR fixed booking fee


def test_api_rbac_idempotency_and_json_duplicate_detection(operator):
    client = Client()
    token = auth.make_tokens(operator)['access_token']
    headers = {'HTTP_AUTHORIZATION': f'Bearer {token}', 'HTTP_IDEMPOTENCY_KEY': 's7-create-key-1234'}
    name = 'S7_HTTP_' + uuid4().hex[:10].upper()
    body = {
        'zone_key': name, 'version': 1, 'name': 'HTTP Zone',
        'center_latitude': '22.5000000', 'center_longitude': '88.3000000',
        'radius_m': 3500, 'restriction_mode': 'STRICT',
        'starts_at': (timezone.now() - timedelta(minutes=1)).isoformat(),
        'reason': 'HTTP acceptance configuration',
    }
    uri = '/api/v1/admin/zones'
    created = client.post(uri, json.dumps(body), content_type='application/json', **headers)
    assert created.status_code == 201, created.content
    replay = client.post(uri, json.dumps(body), content_type='application/json', **headers)
    assert replay.status_code == 201 and replay.json() == created.json()
    assert ServiceZone.objects.filter(zone_key=name).count() == 1
    assert client.get(uri, HTTP_AUTHORIZATION=f'Bearer {token}').status_code == 200
    assert AuditEvent.objects.filter(target_type='ServiceZone', action='ZONE_DRAFT_CREATED').exists()
    # User input has already gone through framework JSON decoding by the time
    # Ninja constructs a Schema; check the original bytes before a mutation.
    bad = json.dumps(body).replace('"version": 1', '"version": 1, "version": 2', 1)
    duplicate = client.post(uri, bad, content_type='application/json',
                            HTTP_AUTHORIZATION=f'Bearer {token}',
                            HTTP_IDEMPOTENCY_KEY='s7-duplicate-12345')
    assert duplicate.status_code == 422

    reader_account = Account.objects.create(email=f's7-reader-{uuid4().hex}@example.invalid',
                                            password_hash='not-a-login', email_verified=True)
    reader = Principal.objects.create(account=reader_account, plane='admin', status='ACTIVE')
    AdminRole.objects.create(principal=reader, role='READ_ONLY')
    read_token = auth.make_tokens(reader)['access_token']
    denied = client.post(uri, json.dumps({**body, 'zone_key': name + '_OTHER'}),
                         content_type='application/json',
                         HTTP_AUTHORIZATION=f'Bearer {read_token}',
                         HTTP_IDEMPOTENCY_KEY='s7-reader-key-1234')
    assert denied.status_code == 403
    assert client.get(uri, HTTP_AUTHORIZATION=f'Bearer {read_token}').status_code == 200


def test_assignment_close_uses_database_statement_clock(operator):
    zone, start = zone_fixture(operator)
    suffix = uuid4().hex[:10]
    charger = Charger.objects.create(asset_tag='s7-close-' + suffix,
                                     hal_identity='s7-close-hal-' + suffix,
                                     manufacturer='Test', model='Assignment')
    created = zone_admin.assign_zone(operator, 'charger', {
        'zone_id': zone.id, 'owner_id': charger.id, 'starts_at': start,
        'ends_at': None, 'authorization_ref': 'REVIEW:CLOSE',
        'reason': 'Reviewed charger assignment'})
    closed = zone_admin.close_assignment(operator, 'charger', UUID(created['id']),
                                        'Close old charger assignment')
    assert closed['ends_at'] is not None
    assert closed['ends_at'] > created['starts_at']
    with pytest.raises(DomainError):
        zone_admin.close_assignment(operator, 'charger', UUID(created['id']),
                                    'Cannot close twice')


def test_approved_tax_cannot_close_under_published_tariff(operator):
    zone, start = zone_fixture(operator)
    gst_fixture(operator, zone, start, 'ON_SITE_QR')
    tax = ZoneGSTAssignment.objects.get(zone=zone, channel='ON_SITE_QR')
    published = tariff_fixture(operator, zone, start, 'ON_SITE_QR')
    with pytest.raises(DomainError):
        zone_admin.close_gst(operator, tax.id, 'Cannot withdraw current tax')
    pricing_admin.withdraw(operator, published.id, 2, 'Withdraw composite pricing policy')
    closed = zone_admin.close_gst(operator, tax.id, 'Close approved tax decision')
    assert closed['ends_at'] is not None
    assert ZoneGSTAssignment.objects.get(pk=tax.id).status == 'APPROVED'
