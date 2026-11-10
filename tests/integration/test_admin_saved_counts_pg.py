"""Admin directory saved counts must not include active unsaved transient resources."""
from decimal import Decimal
from uuid import uuid4

import pytest
from django.test import Client

from cow.common.errors import DomainError
from cow.models import (
    Account, AdminOverride, AdminRole, CustomerAddress, CustomerVehicle, Principal,
)
from cow.services import admin_directory, auth

pytestmark = [pytest.mark.postgres, pytest.mark.django_db]


def _principal(plane, *, role=None):
    account = Account.objects.create(
        email=f"s9-saved-{uuid4().hex}@example.invalid",
        password_hash="not-for-login", email_verified=True,
    )
    principal = Principal.objects.create(account=account, plane=plane, status="ACTIVE")
    if role:
        AdminRole.objects.create(principal=principal, role=role)
    return principal


def test_saved_counts_exclude_active_unsaved_and_inactive_records():
    admin = _principal("admin", role="FULL_ADMIN")
    customer = _principal("app")
    for saved, active in [(True, True), (False, True), (True, False)]:
        CustomerVehicle.objects.create(
            owner=customer, make="Demo", model="EV", saved=saved, active=active,
        )
        CustomerAddress.objects.create(
            owner=customer, address="Test fixture street", latitude=Decimal("22.5000000"),
            longitude=Decimal("88.3000000"), saved=saved, active=active,
        )
    expected = {"saved_vehicle_count": 1, "saved_address_count": 1}
    data = admin_directory.customer_detail(admin, customer.id)
    for k, v in expected.items():
        assert data[k] == v

    token = auth.make_tokens(admin)["access_token"]
    response = Client().get(
        f"/api/v1/admin/customers/{customer.id}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200, response.content
    for k, v in expected.items():
        assert response.json()["data"][k] == v


def test_denied_admin_cannot_read_customer_counts():
    admin = _principal("admin", role="FULL_ADMIN")
    customer = _principal("app")
    AdminOverride.objects.create(principal=admin, capability="users.read", decision="DENY")
    with pytest.raises(DomainError) as error:
        admin_directory.customer_detail(admin, customer.id)
    assert error.value.status_code == 403
