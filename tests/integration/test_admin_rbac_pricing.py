"""Real PostgreSQL acceptance: authorization, commercial immutability and races."""

import json
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from cow.models import Account, AdminOverride, AdminRole, AuditEvent, PricingPolicy, Principal
from cow.services import auth

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def principal(label, *, role="FULL_ADMIN", plane="admin"):
    account = Account.objects.create(
        email=f"wp31c-{label}@example.test", password_hash="x", email_verified=True
    )
    obj = Principal.objects.create(account=account, plane=plane, status="ACTIVE")
    if role:
        AdminRole.objects.create(principal=obj, role=role)
    return obj


def headers(actor, *, key="w" * 16, version=None):
    data = {
        "HTTP_AUTHORIZATION": f"Bearer {auth.make_tokens(actor)['access_token']}",
        "HTTP_IDEMPOTENCY_KEY": key,
    }
    if version is not None:
        data["HTTP_IF_MATCH"] = f'"{version}"'
    return data


def data(payload):
    return json.dumps(payload)


def draft(key="STANDARD", version=1, *, scope=None, starts=None, ends=None):
    return {
        "policy_key": key,
        "policy_version": version,
        "scope": scope or {"kind": "SERVICE_ZONE", "key": "ZONE_7"},
        "starts_at": (starts or timezone.now() + timedelta(days=1)).isoformat(),
        "ends_at": ends.isoformat() if ends else None,
        "currency": "INR",
        "reason": "Review of commercial pricing revision",
        "rules": [
            {
                "id": "VISIT",
                "kind": "BASE",
                "component": "VISIT",
                "basis": "VISITS",
                "rate": "17.910001",
                "tax_class": "GST_PENDING",
                "priority": 1,
            }
        ],
    }


def post(client, path, payload, actor, *, key="w" * 16, version=None):
    return client.post(
        path,
        data(payload),
        content_type="application/json",
        **headers(actor, key=key, version=version),
    )


def test_rbac_catalog_grants_deny_and_last_admin_guards():
    actor = principal("root")
    target = principal("target", role="READ_ONLY")
    viewer = principal("viewer", role="READ_ONLY")
    client = Client()
    catalog = client.get("/api/v1/admin/rbac/catalog", **headers(actor))
    assert catalog.status_code == 200
    assert "charging.command" in catalog.json()["data"]["capabilities"]
    assert client.get("/api/v1/admin/rbac/catalog", **headers(viewer)).status_code == 200
    assert (
        client.get(
            f"/api/v1/admin/rbac/accounts/{target.account_id}", **headers(viewer)
        ).status_code
        == 200
    )
    forbidden = post(
        client,
        f"/api/v1/admin/rbac/accounts/{target.account_id}/roles",
        {"role": "OPERATIONS", "reason": "Enable dispatch operations"},
        viewer,
    )
    assert forbidden.status_code == 403
    self_modify = post(
        client,
        f"/api/v1/admin/rbac/accounts/{actor.account_id}/roles",
        {"role": "SUPPORT", "reason": "Do not self elevate"},
        actor,
    )
    assert self_modify.status_code == 409
    granted = post(
        client,
        f"/api/v1/admin/rbac/accounts/{target.account_id}/roles",
        {"role": "OPERATIONS", "reason": "Grant dispatch operations"},
        actor,
    )
    assert granted.status_code == 200
    assert AdminRole.objects.filter(principal=target, role="OPERATIONS").count() == 1
    override_path = f"/api/v1/admin/rbac/accounts/{target.account_id}/overrides/fleet.manage"
    denied = client.put(
        override_path,
        data({"decision": "DENY", "reason": "Suspend fleet write access"}),
        content_type="application/json",
        **headers(actor),
    )
    assert denied.status_code == 200
    snapshot = client.get(f"/api/v1/admin/rbac/accounts/{target.account_id}", **headers(actor))
    assert "fleet.manage" not in snapshot.json()["data"]["effective_capabilities"]
    removed = client.delete(
        override_path,
        data({"reason": "Restore default role access"}),
        content_type="application/json",
        **headers(actor),
    )
    assert removed.status_code == 200
    assert not AdminOverride.objects.filter(principal=target, capability="fleet.manage").exists()
    assert AuditEvent.objects.filter(target_type="AdminRBAC").count() >= 3
    # Keep a manager who can grant, but is not himself a Full Admin.
    # The sole Full Admin cannot be removed or directly DENY-locked out.
    sole = principal("sole-full")
    AdminRole.objects.filter(principal=actor).delete()
    AdminOverride.objects.create(
        principal=actor, capability="system.settings.manage", decision="ALLOW"
    )
    last = client.delete(
        f"/api/v1/admin/rbac/accounts/{sole.account_id}/roles/FULL_ADMIN",
        data({"reason": "Attempt final admin revocation"}),
        content_type="application/json",
        **headers(actor),
    )
    assert last.status_code == 409
    denied_last = client.put(
        f"/api/v1/admin/rbac/accounts/{sole.account_id}/overrides/system.settings.manage",
        data({"decision": "DENY", "reason": "Attempt to remove final manager"}),
        content_type="application/json",
        **headers(actor),
    )
    assert denied_last.status_code == 409


def test_generic_pricing_endpoints_are_retired():
    actor = principal("pricing-retired")
    client = Client()
    path = "/api/v1/admin/pricing/policies"
    assert client.get(path, **headers(actor)).status_code == 410
    response = post(client, path, draft(), actor)
    assert response.status_code == 410, response.content
    assert response.json()["error"]["code"] == "GENERIC_PRICING_RETIRED"
    example_id = "00000000-0000-4000-8000-000000000001"
    item = f"{path}/{example_id}"
    assert client.get(item, **headers(actor)).status_code == 410
    assert client.get(item + "/history", **headers(actor)).status_code == 410
    assert post(client, item + "/publish", {"reason": "Retired mode"}, actor, version=1).status_code == 410
    assert post(client, item + "/withdraw", {"reason": "Retired mode"}, actor, version=2).status_code == 410
    assert not PricingPolicy.objects.filter(policy_key="STANDARD").exists()
