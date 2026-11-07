"""Sprint 9 Admin groups/directories acceptance on a disposable PostgreSQL DB.

No tests modify the real Aiven database. Run only through the existing isolated
PostgreSQL 17 harness after verifying migration 0010 is installed.
"""
from uuid import uuid4

import pytest
from django.test import Client
from cow.common.errors import DomainError
from cow.models import Account, AdminRole, CommercialGroupMembership, CustomerProfile, DriverProfile, Principal
from cow.services import admin_directory, admin_groups, auth, commercial_admin

pytestmark = [pytest.mark.postgres, pytest.mark.django_db]


def _identity(plane, *, role=None):
    account = Account.objects.create(
        email=f's9-{uuid4().hex}@example.invalid',
        password_hash='not-for-login', email_verified=True)
    principal = Principal.objects.create(account=account, plane=plane, status='ACTIVE')
    if role:
        AdminRole.objects.create(principal=principal, role=role)
    return principal


def test_admin_customer_and_driver_directory_identity_and_plane_isolation():
    admin = _identity('admin', role='FULL_ADMIN')
    customer = _identity('app')
    driver = _identity('driver')
    profile = CustomerProfile.objects.create(principal=customer, full_name='Test Customer')
    DriverProfile.objects.create(principal=driver, eligibility='PENDING')
    cp = admin_directory.list_customers(admin, email=customer.account.email)
    dp = admin_directory.list_drivers(admin, email=driver.account.email)
    assert [x['customer_id'] for x in cp['items']] == [str(customer.pk)]
    assert [x['driver_id'] for x in dp['items']] == [str(driver.pk)]
    assert cp['items'][0]['profile_id'] == str(profile.pk)
    assert admin_directory.customer_detail(admin, customer.pk)['saved_vehicle_count'] == 0
    assert admin_directory.driver_detail(admin, driver.pk)['active_duty_count'] == 0
    with pytest.raises(DomainError) as e:
        admin_directory.customer_detail(admin, driver.pk)
    assert e.value.status_code == 404
    with pytest.raises(DomainError) as e:
        admin_directory.driver_detail(admin, customer.pk)
    assert e.value.status_code == 404


def test_group_membership_history_removal_and_blocked_deactivation():
    admin = _identity('admin', role='FULL_ADMIN')
    customer = _identity('app')
    profile = CustomerProfile.objects.create(principal=customer, full_name='Group Member')
    group = commercial_admin.create_group(admin, 's9-' + uuid4().hex[:12], '', 'Verified admin reason')
    group_id = group['id']
    commercial_admin.change_membership(admin, profile.pk, group_id, 'Verified assignment reason')
    assert admin_groups.group_detail(admin, group_id)['active_member_count'] == 1
    members = admin_groups.group_members(admin, group_id)
    assert len(members['items']) == 1
    assert members['items'][0]['customer_id'] == str(customer.pk)
    with pytest.raises(DomainError) as exc:
        admin_groups.set_group_active(admin, group_id, False, 'Verified block reason')
    assert exc.value.status_code == 409
    result = admin_groups.remove_member(admin, profile.pk, 'Verified removal reason')
    assert result['removed'] is True
    repeat = admin_groups.remove_member(admin, profile.pk, 'Verified removal reason')
    assert repeat['already_absent'] is True
    assert admin_groups.group_members(admin, group_id)['items'] == []
    assert len(admin_groups.group_members(admin, group_id, include_history=True)['items']) == 1
    assert CommercialGroupMembership.objects.filter(customer=profile, ends_at__isnull=False).count() == 1
    assert admin_groups.set_group_active(admin, group_id, False, 'Verified closure reason')['is_active'] is False
    assert admin_groups.set_group_active(admin, group_id, True, 'Verified reopen reason')['is_active'] is True


def test_http_contract_and_cross_role_mutation_denial():
    admin = _identity('admin', role='FULL_ADMIN')
    readonly = _identity('admin', role='READ_ONLY')
    customer = _identity('app')
    profile = CustomerProfile.objects.create(principal=customer, full_name='API Member')
    group = commercial_admin.create_group(admin, 's9-' + uuid4().hex[:12], '', 'Verified admin reason')
    client = Client()
    authz={'HTTP_AUTHORIZATION': 'Bearer ' + auth.make_tokens(admin)['access_token']}
    for path in ['/api/v1/admin/customers', '/api/v1/admin/drivers',
                 '/api/v1/admin/commercial/groups/'+group['id'],
                 '/api/v1/admin/commercial/groups/'+group['id']+'/members']:
        response=client.get(path, **authz)
        assert response.status_code == 200, (path,response.content)
    read_authz={'HTTP_AUTHORIZATION': 'Bearer '+auth.make_tokens(readonly)['access_token']}
    response=client.post('/api/v1/admin/commercial/groups/'+group['id']+'/deactivate',
        data='{"reason":"Verified admin reason"}', content_type='application/json',
        HTTP_IDEMPOTENCY_KEY='management-gate-' + uuid4().hex,
        **read_authz)
    assert response.status_code == 403, response.content
    response=client.get('/api/v1/admin/customers/'+str(customer.pk), **authz)
    assert response.status_code == 200
    assert response.json()['data']['profile_id']==str(profile.pk)
