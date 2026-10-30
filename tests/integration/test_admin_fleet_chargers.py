"""PostgreSQL-only acceptance for Admin charger, connector, and pairing parity."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client
from django.utils import timezone

from cow.errors import DomainError
from cow.models import (
    Account,
    AdminOverride,
    AdminRole,
    Charger,
    ChargerConnector,
    ChargerMaintenancePeriod,
    Principal,
    ServiceVehicle,
    VehicleChargerPairing,
)
from cow.services import auth
from cow.services import fleet as fleet_service

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def admin_principal(*, role="FULL_ADMIN"):
    account = Account.objects.create(
        email=f"charger-{Account.objects.count()}@example.test",
        password_hash="x",
        email_verified=True,
    )
    principal = Principal.objects.create(account=account, plane="admin", status="ACTIVE")
    AdminRole.objects.create(principal=principal, role=role)
    return principal


def headers(principal, *, key="c" * 16, etag=None):
    result = {
        "HTTP_AUTHORIZATION": f"Bearer {auth.make_tokens(principal)['access_token']}",
        "HTTP_IDEMPOTENCY_KEY": key,
    }
    if etag is not None:
        result["HTTP_IF_MATCH"] = f'"{etag}"'
    return result


def charger_payload(**overrides):
    value = {
        "asset_tag": "CHARGER-001",
        "serial_number": "SERIAL-001",
        "manufacturer": "TransEV",
        "model": "Fast DC",
        "rated_power_w": 20000,
        "protocol_family": "OCPP",
        "protocol_version": "2.0.1",
        "hal_identity": "hal-charger-001",
        "notes": "Primary demonstration charger",
    }
    value.update(overrides)
    return value


def connector_payload(**overrides):
    value = {
        "connector_number": 1,
        "connector_type": "CCS2",
        "max_power_w": 20000,
        "supports_ac": False,
        "supports_dc": True,
        "is_active": True,
    }
    value.update(overrides)
    return value


def test_charger_and_connector_round_trip_idempotency_etag_and_lifecycle():
    principal = admin_principal()
    client = Client()
    created = client.post(
        "/api/v1/admin/chargers",
        json.dumps(charger_payload()),
        content_type="application/json",
        **headers(principal),
    )
    assert created.status_code == 201, created.content
    charger = created.json()["data"]
    assert created["ETag"] == '"1"'
    replay = client.post(
        "/api/v1/admin/chargers",
        json.dumps(charger_payload()),
        content_type="application/json",
        **headers(principal),
    )
    assert replay.status_code == 201 and replay.json() == created.json()
    reused = client.post(
        "/api/v1/admin/chargers",
        json.dumps(charger_payload(model="Changed")),
        content_type="application/json",
        **headers(principal),
    )
    assert reused.status_code == 409
    listed = client.get("/api/v1/admin/chargers?limit=1&status=AVAILABLE", **headers(principal))
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["id"] == charger["id"]
    loaded = client.get(f"/api/v1/admin/chargers/{charger['id']}", **headers(principal))
    assert loaded.status_code == 200 and loaded["ETag"] == '"1"'
    missing = client.patch(
        f"/api/v1/admin/chargers/{charger['id']}",
        json.dumps({"notes": "changed"}),
        content_type="application/json",
        **headers(principal, key="m" * 16),
    )
    assert missing.status_code == 428
    updated = client.patch(
        f"/api/v1/admin/chargers/{charger['id']}",
        json.dumps({"notes": "changed"}),
        content_type="application/json",
        **headers(principal, key="u" * 16, etag=1),
    )
    assert updated.status_code == 200 and updated["ETag"] == '"2"'
    stale = client.patch(
        f"/api/v1/admin/chargers/{charger['id']}",
        json.dumps({"notes": "stale"}),
        content_type="application/json",
        **headers(principal, key="s" * 16, etag=1),
    )
    assert stale.status_code == 412

    connector = client.post(
        f"/api/v1/admin/chargers/{charger['id']}/connectors",
        json.dumps(connector_payload()),
        content_type="application/json",
        **headers(principal, key="n" * 16),
    )
    assert connector.status_code == 201 and connector["ETag"] == '"1"'
    connector_id = connector.json()["data"]["id"]
    connectors = client.get(
        f"/api/v1/admin/chargers/{charger['id']}/connectors", **headers(principal)
    )
    assert connectors.status_code == 200
    assert connectors.json()["data"]["items"][0]["id"] == connector_id
    duplicate_connector = client.post(
        f"/api/v1/admin/chargers/{charger['id']}/connectors",
        json.dumps(connector_payload()),
        content_type="application/json",
        **headers(principal, key="z" * 16),
    )
    assert duplicate_connector.status_code == 409
    invalid_connector = client.post(
        f"/api/v1/admin/chargers/{charger['id']}/connectors",
        json.dumps(connector_payload(connector_number=2, supports_dc=False)),
        content_type="application/json",
        **headers(principal, key="i" * 16),
    )
    assert invalid_connector.status_code == 422
    other_charger = Charger.objects.create(
        asset_tag="CONNECTOR-OTHER", manufacturer="TransEV", model="DC"
    )
    wrong_parent = client.patch(
        f"/api/v1/admin/chargers/{other_charger.id}/connectors/{connector_id}",
        json.dumps({"max_power_w": 17000}),
        content_type="application/json",
        **headers(principal, key="o" * 16, etag=1),
    )
    assert wrong_parent.status_code == 404
    patched_connector = client.patch(
        f"/api/v1/admin/chargers/{charger['id']}/connectors/{connector_id}",
        json.dumps({"max_power_w": 18000}),
        content_type="application/json",
        **headers(principal, key="p" * 16, etag=1),
    )
    assert patched_connector.status_code == 200 and patched_connector["ETag"] == '"2"'
    # An identical long-route retry must replay the stored receipt, not
    # execute the mutation again and trip the stale resource ETag.
    connector_replay = client.patch(
        f"/api/v1/admin/chargers/{charger['id']}/connectors/{connector_id}",
        json.dumps({"max_power_w": 18000}),
        content_type="application/json",
        **headers(principal, key="p" * 16, etag=1),
    )
    assert connector_replay.status_code == 200
    assert connector_replay.json() == patched_connector.json()
    assert connector_replay["ETag"] == '"2"'

    lifecycle = client.post(
        f"/api/v1/admin/chargers/{charger['id']}/deactivate",
        json.dumps({"reason": "Scheduled electrical inspection"}),
        content_type="application/json",
        **headers(principal, key="d" * 16, etag=2),
    )
    assert lifecycle.status_code == 200 and lifecycle["ETag"] == '"3"'
    reactivated = client.post(
        f"/api/v1/admin/chargers/{charger['id']}/reactivate",
        json.dumps({"reason": "Inspection was completed safely"}),
        content_type="application/json",
        **headers(principal, key="r" * 16, etag=3),
    )
    assert reactivated.status_code == 200 and reactivated["ETag"] == '"4"'
    now = timezone.now()
    maintenance = client.post(
        f"/api/v1/admin/chargers/{charger['id']}/maintenance",
        json.dumps(
            {
                "starts_at": (now - timedelta(minutes=1)).isoformat(),
                "ends_at": (now + timedelta(minutes=5)).isoformat(),
                "reason": "Planned live safety inspection",
            }
        ),
        content_type="application/json",
        **headers(principal, key="w" * 16),
    )
    assert maintenance.status_code == 201
    availability = client.get(
        f"/api/v1/admin/chargers/{charger['id']}/availability", **headers(principal)
    )
    assert availability.status_code == 200
    assert availability.json()["data"]["inventory_state"] == "MAINTENANCE"
    assert availability.json()["data"]["hal_connectivity"] == "NOT_INTEGRATED"
    blocked = client.post(
        f"/api/v1/admin/chargers/{charger['id']}/deactivate",
        json.dumps({"reason": "Take asset out of service now"}),
        content_type="application/json",
        **headers(principal, key="x" * 16, etag=4),
    )
    assert blocked.status_code == 200
    no_reactivate = client.post(
        f"/api/v1/admin/chargers/{charger['id']}/reactivate",
        json.dumps({"reason": "Cannot reactivate during maintenance"}),
        content_type="application/json",
        **headers(principal, key="y" * 16, etag=5),
    )
    assert no_reactivate.status_code == 409
    history = client.get(f"/api/v1/admin/chargers/{charger['id']}/history", **headers(principal))
    assert history.status_code == 200
    assert {event["action"] for event in history.json()["data"]["items"]} >= {
        "CREATE",
        "MAINTENANCE",
    }


def test_pairing_exclusivity_end_history_and_charger_lifecycle_guard():
    principal = admin_principal()
    client = Client()
    vehicle = ServiceVehicle.objects.create(asset_tag="PAIR-VEHICLE", make="TransEV", model="Van")
    charger = Charger.objects.create(
        asset_tag="PAIR-CHARGER",
        manufacturer="TransEV",
        model="DC",
        rated_power_w=20000,
    )
    body = {
        "vehicle_id": str(vehicle.id),
        "charger_id": str(charger.id),
        "reason": "Install approved charging pair",
    }
    paired = client.post(
        "/api/v1/admin/pairings",
        json.dumps(body),
        content_type="application/json",
        **headers(principal),
    )
    assert paired.status_code == 201, paired.content
    pairing_id = paired.json()["data"]["id"]
    replay = client.post(
        "/api/v1/admin/pairings",
        json.dumps(body),
        content_type="application/json",
        **headers(principal),
    )
    assert replay.status_code == 201 and replay.json() == paired.json()
    other_vehicle = ServiceVehicle.objects.create(
        asset_tag="PAIR-OTHER", make="TransEV", model="Van"
    )
    collision = client.post(
        "/api/v1/admin/pairings",
        json.dumps({**body, "vehicle_id": str(other_vehicle.id)}),
        content_type="application/json",
        **headers(principal, key="q" * 16),
    )
    assert collision.status_code == 409
    listed = client.get(
        f"/api/v1/admin/pairings?charger_id={charger.id}&status=ACTIVE", **headers(principal)
    )
    assert listed.status_code == 200 and listed.json()["data"]["items"][0]["id"] == pairing_id
    blocked = client.post(
        f"/api/v1/admin/chargers/{charger.id}/deactivate",
        json.dumps({"reason": "Cannot deactivate a paired charger"}),
        content_type="application/json",
        **headers(principal, key="b" * 16, etag=1),
    )
    assert blocked.status_code == 409
    ended = client.post(
        f"/api/v1/admin/pairings/{pairing_id}/end",
        json.dumps({"reason": "Pair safely removed after service"}),
        content_type="application/json",
        **headers(principal, key="e" * 16),
    )
    assert ended.status_code == 200
    assert ended.json()["data"]["status"] == "ENDED"
    ended_record = VehicleChargerPairing.objects.get(id=pairing_id)
    assert ended_record.effective_to and ended_record.effective_to > ended_record.effective_from
    repeat_end = client.post(
        f"/api/v1/admin/pairings/{pairing_id}/end",
        json.dumps({"reason": "Pair safely removed after service"}),
        content_type="application/json",
        **headers(principal, key="e" * 16),
    )
    assert repeat_end.status_code == 200
    with pytest.raises(IntegrityError):
        VehicleChargerPairing.objects.filter(id=pairing_id).update(reason="rewritten")


def test_charger_permissions_and_parallel_pairing_and_maintenance_are_fail_closed():
    assert Client().get("/api/v1/admin/chargers").status_code == 401
    principal = admin_principal()
    AdminOverride.objects.create(principal=principal, capability="fleet.read", decision="DENY")
    denied = Client().get("/api/v1/admin/chargers", **headers(principal))
    assert denied.status_code == 403 and Charger.objects.count() == 0
    wrong_account = Account.objects.create(
        email="charger-wrong-plane@example.test", password_hash="x", email_verified=True
    )
    wrong_principal = Principal.objects.create(account=wrong_account, plane="app", status="ACTIVE")
    wrong = Client().get("/api/v1/admin/chargers", **headers(wrong_principal))
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "PRINCIPAL_PLANE_MISMATCH"

    readonly = admin_principal(role="READ_ONLY")
    no_key = Client().post(
        "/api/v1/admin/chargers",
        json.dumps(charger_payload(asset_tag="NO-KEY")),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {auth.make_tokens(readonly)['access_token']}",
    )
    assert no_key.status_code == 403
    assert Charger.objects.count() == 0

    manager = admin_principal()
    missing_key = Client().post(
        "/api/v1/admin/chargers",
        json.dumps(charger_payload(asset_tag="MISSING-KEY")),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {auth.make_tokens(manager)['access_token']}",
    )
    assert missing_key.status_code == 428
    assert Charger.objects.count() == 0
    charger = Charger.objects.create(asset_tag="RACE-CHARGER", manufacturer="TransEV", model="DC")
    first = ServiceVehicle.objects.create(asset_tag="RACE-VEHICLE-1", make="TransEV", model="Van")
    second = ServiceVehicle.objects.create(asset_tag="RACE-VEHICLE-2", make="TransEV", model="Van")

    def pair(vehicle):
        close_old_connections()
        try:
            with transaction.atomic():
                return fleet_service.create_pairing(
                    manager, vehicle.id, charger.id, "Concurrent pairing request"
                )
        except Exception as exc:
            return exc
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(pair, [first, second]))
    assert sum(isinstance(value, VehicleChargerPairing) for value in outcomes) == 1
    assert sum(isinstance(value, Exception) for value in outcomes) == 1

    now = timezone.now()
    fleet_service.end_pairing(
        manager,
        next(value.id for value in outcomes if isinstance(value, VehicleChargerPairing)),
        "Close race pair safely",
    )
    first_period = fleet_service.add_charger_maintenance(
        manager,
        charger.id,
        now,
        now + timedelta(minutes=10),
        "Concurrent maintenance window one",
    )
    with pytest.raises(DomainError) as overlap:
        fleet_service.add_charger_maintenance(
            manager,
            charger.id,
            now + timedelta(minutes=1),
            now + timedelta(minutes=11),
            "Concurrent maintenance window two",
        )
    assert overlap.value.status_code == 409
    assert ChargerMaintenancePeriod.objects.get(id=first_period.id).reason
    with pytest.raises(IntegrityError):
        ChargerMaintenancePeriod.objects.create(
            charger=charger,
            starts_at=now + timedelta(minutes=1),
            ends_at=now + timedelta(minutes=11),
            reason="Direct overlapping maintenance write",
            created_by=manager,
        )
    with pytest.raises(IntegrityError):
        ChargerMaintenancePeriod.objects.filter(id=first_period.id).update(reason="rewritten")

    race_charger = Charger.objects.create(
        asset_tag="MAINT-RACE-CHARGER", manufacturer="TransEV", model="DC"
    )

    def add_maintenance(reason):
        close_old_connections()
        try:
            with transaction.atomic():
                return fleet_service.add_charger_maintenance(
                    manager,
                    race_charger.id,
                    now,
                    now + timedelta(minutes=10),
                    reason,
                )
        except Exception as exc:
            return exc
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        maintenance_outcomes = list(
            pool.map(add_maintenance, ["Concurrent maintenance one", "Concurrent maintenance two"])
        )
    assert sum(isinstance(value, ChargerMaintenancePeriod) for value in maintenance_outcomes) == 1
    assert sum(isinstance(value, Exception) for value in maintenance_outcomes) == 1
    assert ChargerConnector.objects.count() == 0
