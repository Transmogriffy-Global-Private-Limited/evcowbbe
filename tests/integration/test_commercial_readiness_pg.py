"""Commercial readiness must evaluate full timestamps at PostgreSQL COMMIT."""
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError
from django.utils import timezone

from cow.models import Account, AdminRole, Principal
from cow.services import commercial_admin as cm, zone_admin

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def test_commercial_activation_proves_timestamp_and_last_default_guards():
    uid = uuid4().hex.upper()
    reason = 'Reviewed timestamp readiness regression'
    account = Account.objects.create(
        email=f'cm-ready-{uid.lower()}@example.invalid',
        password_hash='not-a-login', email_verified=True,
    )
    admin = Principal.objects.create(account=account, plane='admin', status='ACTIVE')
    AdminRole.objects.create(principal=admin, role='FULL_ADMIN')
    zone = zone_admin.create_zone(admin, {
        'zone_key': 'CMR_' + uid[:14], 'version': 1, 'name': 'Commercial readiness regression',
        'center_latitude': '22.5000000', 'center_longitude': '88.3000000',
        'radius_m': 5000, 'restriction_mode': 'STRICT',
        'starts_at': timezone.now() - timedelta(hours=2), 'ends_at': None, 'reason': reason,
    })
    zid = UUID(zone['id'])
    zone_admin.transition_zone(admin, zid, 'publish', reason)
    plan = {
        'key': 'CM_READY_' + uid[:14], 'version': 1, 'channel': 'ROAMING',
        'currency': 'INR', 'price_mode': 'EXCLUSIVE',
        'profiles': {'INSIDE': {'charging': {
            'basis': 'ENERGY_KWH', 'rate': '20', 'tax_class': 'EV_SERVICE',
        }}},
    }
    draft = cm.create_tariff(admin, plan, reason)
    cm.publish_tariff(admin, UUID(draft['id']), 1, reason)
    classes = {'EV_SERVICE': {'treatment': 'TAXABLE', 'supply_group': 'EV_SUPPLY',
                'rates': {'CGST': '9', 'SGST': '9', 'UTGST': '0', 'IGST': '0'}}}
    gst = cm.create_gst_profile(admin, 'CM_GST_' + uid[:14], 1,
                                'SELLER_TEST', 'LEGAL_ACCEPTANCE_ONLY', classes, reason)
    cm.approve_gst_profile(admin, UUID(gst['id']), reason)
    tariff_binding = cm.assign_tariff(admin, UUID(draft['id']), 'ZONE', zid,
                                      'ROAMING', 'DEFAULT', None, None, reason)
    gst_binding = cm.assign_gst(admin, UUID(gst['id']), zid, 'ROAMING', None, None, reason)
    assert cm.channel_readiness(admin, zid, 'ROAMING')['configuration_ready']
    assert cm.set_channel(admin, zid, 'ROAMING', True, reason)['enabled']
    with pytest.raises(IntegrityError):
        cm.close_tariff_assignment(admin, UUID(tariff_binding['id']), reason)
    with pytest.raises(IntegrityError):
        cm.close_gst_assignment(admin, UUID(gst_binding['id']), reason)
    assert cm.channel_readiness(admin, zid, 'ROAMING')['configuration_ready']
