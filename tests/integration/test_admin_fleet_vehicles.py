"""PostgreSQL-only acceptance for the nine Admin service-vehicle operations."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import close_old_connections, transaction
from django.test import Client
from django.utils import timezone

from cow.errors import DomainError
from cow.models import (
    Account,
    AdminOverride,
    AdminRole,
    Charger,
    DriverProfile,
    DutySession,
    Principal,
    ServiceVehicle,
    VehicleChargerPairing,
    VehicleMaintenancePeriod,
)
from cow.services import auth
from cow.services import fleet as fleet_service

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def admin_principal(*, role="FULL_ADMIN"):
    account = Account.objects.create(
        email=f"fleet-{Account.objects.count()}@example.test",
        password_hash="x",
        email_verified=True,
    )
    principal = Principal.objects.create(account=account, plane="admin", status="ACTIVE")
    AdminRole.objects.create(principal=principal, role=role)
    return principal


def headers(principal, *, key="k" * 16, etag=None):
    result = {
        "HTTP_AUTHORIZATION": f"Bearer {auth.make_tokens(principal)['access_token']}",
        "HTTP_IDEMPOTENCY_KEY": key,
    }
    if etag is not None:
        result["HTTP_IF_MATCH"] = f'"{etag}"'
    return result


def payload(**overrides):
    result = {
        "asset_tag": "FLEET-001",
        "make": "TransEV",
        "model": "Mobile Charger",
        "inspection_status": "VALID",
    }
    result.update(overrides)
    return result


def test_create_list_read_patch_idempotency_and_preconditions():
    principal = admin_principal()
    client = Client()
    created = client.post(
        "/api/v1/admin/vehicles",
        json.dumps(payload()),
        content_type="application/json",
        **headers(principal),
    )
    assert created.status_code == 201, created.content
    vehicle = created.json()["data"]
    assert created["ETag"] == '"1"'
    repeated = client.post(
        "/api/v1/admin/vehicles",
        json.dumps(payload()),
        content_type="application/json",
        **headers(principal),
    )
    assert repeated.status_code == 201 and repeated.json() == created.json()
    conflict = client.post(
        "/api/v1/admin/vehicles",
        json.dumps(payload(model="Changed")),
        content_type="application/json",
        **headers(principal),
    )
    assert conflict.status_code == 409
    listed = client.get("/api/v1/admin/vehicles?limit=1&status=AVAILABLE", **headers(principal))
    assert listed.status_code == 200 and listed.json()["data"]["items"][0]["id"] == vehicle["id"]
    read = client.get(f"/api/v1/admin/vehicles/{vehicle['id']}", **headers(principal))
    assert read.status_code == 200 and read["ETag"] == '"1"'
    missing = client.patch(
        f"/api/v1/admin/vehicles/{vehicle['id']}",
        json.dumps({"notes": "changed"}),
        content_type="application/json",
        **headers(principal, key="m" * 16),
    )
    assert missing.status_code == 428
    updated = client.patch(
        f"/api/v1/admin/vehicles/{vehicle['id']}",
        json.dumps({"notes": "changed"}),
        content_type="application/json",
        **headers(principal, key="u" * 16, etag=1),
    )
    assert updated.status_code == 200 and updated["ETag"] == '"2"'
    stale = client.patch(
        f"/api/v1/admin/vehicles/{vehicle['id']}",
        json.dumps({"notes": "again"}),
        content_type="application/json",
        **headers(principal, key="s" * 16, etag=1),
    )
    assert stale.status_code == 412


def test_deny_wrong_plane_and_failed_mutation_have_no_effects():
    principal = admin_principal()
    AdminOverride.objects.create(principal=principal, capability="fleet.read", decision="DENY")
    response = Client().get("/api/v1/admin/vehicles", **headers(principal))
    assert response.status_code == 403 and response.json()["error"]["code"] == "CAPABILITY_DENIED"
    assert ServiceVehicle.objects.count() == 0
    account = Account.objects.create(
        email="wrong-plane@example.test", password_hash="x", email_verified=True
    )
    wrong_plane = Principal.objects.create(account=account, plane="app", status="ACTIVE")
    wrong = Client().get("/api/v1/admin/vehicles", **headers(wrong_plane))
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "PRINCIPAL_PLANE_MISMATCH"


def test_maintenance_overlap_lifecycle_history_and_inventory_only_availability():
    principal = admin_principal()
    vehicle = ServiceVehicle.objects.create(asset_tag="FLEET-002", make="TransEV", model="Van")
    now = timezone.now()
    fleet_service.set_active(
        principal, vehicle.id, vehicle.version, "Take asset out of service", False
    )
    vehicle.refresh_from_db()
    period = fleet_service.add_maintenance(
        principal,
        vehicle.id,
        now - timedelta(minutes=5),
        now + timedelta(minutes=5),
        "Scheduled brake inspection",
    )
    with pytest.raises(DomainError) as overlap:
        fleet_service.add_maintenance(
            principal,
            vehicle.id,
            now,
            now + timedelta(hours=1),
            "Conflicting scheduled maintenance",
        )
    assert overlap.value.status_code == 409
    availability = fleet_service.availability(vehicle.id)
    assert availability["inventory_state"] == "MAINTENANCE"
    assert availability["hal_connectivity"] == "NOT_INTEGRATED"
    assert availability["dispatch_eligibility"] == "NOT_EVALUATED"
    with pytest.raises(DomainError) as reactivation:
        fleet_service.set_active(
            principal, vehicle.id, vehicle.version, "Reactivate while maintenance is active", True
        )
    assert reactivation.value.status_code == 409
    assert fleet_service.history(vehicle.id)[0]["action"] == "MAINTENANCE"
    assert VehicleMaintenancePeriod.objects.get(id=period.id).reason == "Scheduled brake inspection"


def test_deactivation_rejects_current_pairing_and_active_duty_claim():
    principal = admin_principal()
    vehicle = ServiceVehicle.objects.create(asset_tag="FLEET-PAIR", make="TransEV", model="Van")
    now = timezone.now()
    charger = Charger.objects.create(
        asset_tag="CHARGER-PAIR",
        hal_identity="hal-pair",
        manufacturer="TransEV",
        model="DC",
        rated_power_w=20000,
    )
    pairing = VehicleChargerPairing.objects.create(
        vehicle=vehicle, charger=charger, effective_from=now
    )
    with pytest.raises(DomainError) as paired:
        fleet_service.set_active(
            principal, vehicle.id, vehicle.version, "Pairing is current", False
        )
    assert paired.value.status_code == 409
    pairing.effective_to = now
    pairing.save(update_fields=["effective_to"])

    driver_account = Account.objects.create(
        email="fleet-driver@example.test", password_hash="x", email_verified=True
    )
    driver_principal = Principal.objects.create(
        account=driver_account, plane="driver", status="ACTIVE"
    )
    driver = DriverProfile.objects.create(principal=driver_principal, eligibility="APPROVED")
    DutySession.objects.create(
        driver=driver,
        vehicle=vehicle,
        charger=charger,
        state="ON_DUTY",
        started_at=now,
        observed_pairing={},
    )
    with pytest.raises(DomainError) as claimed:
        fleet_service.set_active(principal, vehicle.id, vehicle.version, "Duty is current", False)
    assert claimed.value.status_code == 409


def test_concurrent_versioned_mutations_allow_exactly_one_winner():
    principal = admin_principal()
    vehicle = ServiceVehicle.objects.create(asset_tag="FLEET-003", make="TransEV", model="Van")

    def update(note):
        close_old_connections()
        try:
            with transaction.atomic():
                return fleet_service.patch(principal, vehicle.id, 1, {"notes": note}).version
        except Exception as exc:
            return exc
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ["first", "second"]))
    assert sum(value == 2 for value in outcomes) == 1
    assert sum(isinstance(value, Exception) for value in outcomes) == 1
