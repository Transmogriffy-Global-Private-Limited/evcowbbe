"""Real marker-authorized PostgreSQL acceptance of customer plane master data."""

import json
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from cow.models import (
    Account,
    AuditEvent,
    CustomerAddress,
    CustomerProfile,
    CustomerVehicle,
    Principal,
    ServiceRequest,
)
from cow.services import auth

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def principal(plane="app"):
    account = Account.objects.create(email=f"customer-{uuid4().hex}@example.test", password_hash="x")
    return Principal.objects.create(account=account, plane=plane, status="ACTIVE")


def auth_headers(p, *, version=None, key=None):
    result = {"HTTP_AUTHORIZATION": f"Bearer {auth.make_tokens(p)['access_token']}"}
    if version is not None:
        result["HTTP_IF_MATCH"] = f'"{version}"'
    if key is not None:
        result["HTTP_IDEMPOTENCY_KEY"] = key
    return result


def json_call(client, method, path, body, who, *, version=None, key=None):
    return getattr(client, method)(
        path, json.dumps(body), content_type="application/json",
        **auth_headers(who, version=version, key=key),
    )


def test_profile_is_owner_only_versioned_and_auth_fields_immutable():
    app = principal()
    another = principal()
    driver = principal("driver")
    client = Client()
    before = client.get("/api/v1/app/me", **auth_headers(app))
    assert before.status_code == 200
    assert before.json()["data"]["profile"]["version"] == 0
    assert client.get("/api/v1/app/me", **auth_headers(driver)).status_code == 403
    wrong = json_call(client, "patch", "/api/v1/app/profile", {"email": "x@example.test"}, app,
                      version=0, key="x" * 16)
    assert wrong.status_code == 422
    payload = {"full_name": "Test Customer", "phone": "+919876543210"}
    created = json_call(client, "patch", "/api/v1/app/profile", payload, app,
                        version=0, key="a" * 16)
    assert created.status_code == 200, created.content
    assert created["ETag"] == '"1"'
    replay = json_call(client, "patch", "/api/v1/app/profile", payload, app,
                       version=0, key="a" * 16)
    assert replay.status_code == 200 and replay.json() == created.json()
    stale = json_call(client, "patch", "/api/v1/app/profile", {"full_name": "New"}, app,
                      version=0, key="b" * 16)
    assert stale.status_code == 412
    assert CustomerProfile.objects.get(principal=app).full_name == "Test Customer"
    assert not CustomerProfile.objects.filter(principal=another).exists()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CustomerProfile.objects.create(principal=driver, full_name="Invalid")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CustomerProfile.objects.filter(principal=app).update(full_name="Bypass")


def test_vehicle_owner_version_replay_soft_delete_and_snapshots():
    owner, stranger = principal(), principal()
    client = Client()
    payload = {"make": "Example", "model": "EV", "supports_ac": True,
               "supports_dc": False, "battery_capacity_wh": 52000,
               "connector_types": ["ccs2"]}
    created = json_call(client, "post", "/api/v1/app/vehicles", payload, owner)
    assert created.status_code == 201, created.content
    assert created["ETag"] == '"1"'
    ev_id = created.json()["data"]["id"]
    path = f"/api/v1/app/vehicles/{ev_id}"
    assert client.get(path, **auth_headers(stranger)).status_code == 404
    assert client.get(path, **auth_headers(owner)).status_code == 200
    assert client.get("/api/v1/app/vehicles", **auth_headers(stranger)).json()["data"] == []
    malformed = json_call(client, "post", "/api/v1/app/vehicles",
                          {"make": "X", "model": "Y"}, owner)
    assert malformed.status_code == 422
    job_snapshot = created.json()["data"]
    request = ServiceRequest.objects.create(
        customer=owner, vehicle_snapshot=job_snapshot,
        address_snapshot={"address": "Unrelated immutable job address"},
        requested_energy_wh=3000,
    )
    edited = json_call(client, "patch", path, {"variant": "Extended", "label": "Family"},
                       owner, version=1, key="e" * 16)
    assert edited.status_code == 200 and edited["ETag"] == '"2"'
    assert edited.json()["data"]["variant"] == "Extended"
    replay = json_call(client, "patch", path, {"variant": "Extended", "label": "Family"},
                       owner, version=1, key="e" * 16)
    assert replay.status_code == 200 and replay.json() == edited.json()
    stale = json_call(client, "patch", path, {"label": "New"}, owner,
                      version=1, key="f" * 16)
    assert stale.status_code == 412
    wrong_owner = json_call(client, "patch", path, {"label": "Stolen"}, stranger,
                            version=2, key="g" * 16)
    assert wrong_owner.status_code == 404
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CustomerVehicle.objects.filter(id=ev_id).update(label="Unsafe DB bypass")
    removed = client.delete(path, **auth_headers(owner, version=2, key="h" * 16))
    assert removed.status_code == 200 and removed.json()["data"]["active"] is False
    assert client.get(path, **auth_headers(owner)).status_code == 404
    request.refresh_from_db()
    assert request.vehicle_snapshot == job_snapshot
    assert CustomerVehicle.objects.filter(id=ev_id, active=False).exists()
    assert AuditEvent.objects.filter(target_type="CustomerVehicle").count() == 3


def test_address_atomic_default_version_ownership_and_retirement():
    owner, stranger = principal(), principal()
    client = Client()
    p1 = {"address": "21 Lake Road", "latitude": 22.9475572, "longitude": 88.4614943,
          "line1": "21 Lake Road", "city": "Kolkata"}
    first = json_call(client, "post", "/api/v1/app/addresses", p1, owner)
    assert first.status_code == 201, first.content
    a = first.json()["data"]
    assert a["is_default"] is True
    assert a["latitude"] == "22.9475572"
    p2 = {"address": "10 High Street", "latitude": 22.5, "longitude": 88.0}
    second = json_call(client, "post", "/api/v1/app/addresses", p2, owner)
    assert second.status_code == 201
    b = second.json()["data"]
    assert b["is_default"] is False
    assert client.get(f"/api/v1/app/addresses/{b['id']}", **auth_headers(stranger)).status_code == 404
    path_b = f"/api/v1/app/addresses/{b['id']}"
    switched = json_call(client, "post", path_b + "/make-default", {}, owner,
                         version=1, key="d" * 16)
    assert switched.status_code == 200 and switched["ETag"] == '"2"'
    assert CustomerAddress.objects.get(id=a["id"]).version == 2
    assert CustomerAddress.objects.filter(owner=owner, active=True, is_default=True).count() == 1
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CustomerAddress.objects.filter(id=a["id"]).update(is_default=True)
    replay = json_call(client, "post", path_b + "/make-default", {}, owner,
                       version=1, key="d" * 16)
    assert replay.status_code == 200 and replay.json() == switched.json()
    rejected = json_call(client, "patch", path_b, {"saved": False}, owner,
                         version=2, key="i" * 16)
    assert rejected.status_code == 409
    edited = json_call(client, "patch", path_b, {"postal_code": "700001"}, owner,
                       version=2, key="j" * 16)
    assert edited.status_code == 200 and edited["ETag"] == '"3"'
    snapshot = edited.json()["data"]
    req = ServiceRequest.objects.create(
        customer=owner, vehicle_snapshot={"make": "Example"},
        address_snapshot=snapshot, requested_energy_wh=3000,
    )
    removed = client.delete(path_b, **auth_headers(owner, version=3, key="k" * 16))
    assert removed.status_code == 200
    assert removed.json()["data"]["is_default"] is False
    assert client.get(path_b, **auth_headers(owner)).status_code == 404
    assert CustomerAddress.objects.filter(owner=owner, active=True, is_default=True).count() == 0
    req.refresh_from_db()
    assert req.address_snapshot == snapshot
    assert CustomerAddress.objects.get(id=b["id"]).version == 4
    assert client.get("/api/v1/app/addresses", **auth_headers(stranger)).json()["data"] == []


def test_real_service_request_freezes_expanded_snapshots():
    from cow.services.jobs import create_request

    owner = principal()
    ev = CustomerVehicle.objects.create(
        owner=owner, make="Example", model="EV", variant="Long Range",
        battery_capacity_wh=45000, supports_ac=True, supports_dc=True,
    )
    address = CustomerAddress.objects.create(
        owner=owner, address="22 Lake Road", latitude="22.9475572",
        longitude="88.4614943", line1="22 Lake Road", city="Kolkata",
    )
    request = create_request(owner, ev.id, address.id, 5000)
    original_vehicle = request.vehicle_snapshot.copy()
    original_address = request.address_snapshot.copy()
    assert original_vehicle["variant"] == "Long Range"
    assert original_vehicle["battery_capacity_wh"] == 45000
    assert original_vehicle["capability_evidence"] == "UNVERIFIED_CUSTOMER_DATA"
    assert original_address["line1"] == "22 Lake Road"
    assert original_address["city"] == "Kolkata"
    ev.variant = "Changed"
    ev.version += 1
    ev.save(update_fields=["variant", "version", "updated_at"])
    address.city = "Changed"
    address.version += 1
    address.save(update_fields=["city", "version", "updated_at"])
    request.refresh_from_db()
    assert request.vehicle_snapshot == original_vehicle
    assert request.address_snapshot == original_address
