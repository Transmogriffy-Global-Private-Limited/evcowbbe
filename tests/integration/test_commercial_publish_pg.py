"""PostgreSQL regression: lock the policy without locking its optional payload join."""
from uuid import UUID, uuid4

import pytest

from cow.models import Account, AdminRole, Principal
from cow.services import commercial_admin

pytestmark = [pytest.mark.postgres, pytest.mark.django_db]


def test_tariff_publication_locks_pricing_row_only():
    tag = uuid4().hex.upper()
    account = Account.objects.create(
        email=f'cm-lock-{tag.lower()}@example.invalid',
        password_hash='not-a-login',
        email_verified=True,
    )
    principal = Principal.objects.create(account=account, plane='admin', status='ACTIVE')
    AdminRole.objects.create(principal=principal, role='FULL_ADMIN')
    plan = {
        'key': 'CM_LOCK_' + tag[:16],
        'version': 1,
        'channel': 'ROAMING',
        'currency': 'INR',
        'price_mode': 'EXCLUSIVE',
        'profiles': {'INSIDE': {
            'charging': {'basis': 'ENERGY_KWH', 'rate': '20', 'tax_class': 'EV_SERVICE'},
        }},
    }
    draft = commercial_admin.create_tariff(principal, plan, 'Reviewed locking regression')
    published = commercial_admin.publish_tariff(
        principal, UUID(draft['id']), 1, 'Reviewed locking regression'
    )
    assert published['status'] == 'PUBLISHED'
    assert published['state_version'] == 2
