"""CoW-only mapping/connector lifecycle acceptance. SCRATCH PostgreSQL ONLY.

Never execute against Aiven DEV, any shared DB, or a deployed service.
Use the existing isolated PostgreSQL harness and its safety gates.
"""

from copy import deepcopy
import os
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.test import override_settings

from cow.common.errors import DomainError
from cow.hal import v1_machine as machine
from cow.models import (
    Account,
    AdminRole,
    Charger,
    ChargerConnector,
    HALV1ChargerOperation,
    HALV1Mapping,
    Principal,
)

# Test module requires a positively identified disposable PostgreSQL harness.
# An accidental general pytest invocation must not open or mutate Aiven DEV.
if os.environ.get("COW_PG_TEST_HARNESS_READY") != "1":
    pytest.skip("Requires isolated PostgreSQL acceptance harness", allow_module_level=True)
_db = settings.DATABASES.get("default", {})
_host = str(_db.get("HOST", "")).lower()
_name = str(_db.get("NAME", "")).lower()
if "aivencloud.com" in _host or _name in {"defaultdb", "postgres"}:
    raise RuntimeError("Refusing HAL mapping lifecycle tests against unverified database identity")

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]
SCOPE = "f507864d-f5b9-4c6c-9503-0e4b6e72a441"


def setup():
    account = Account.objects.create(
        email=f"halguard-{uuid4().hex}@example.invalid",
        password_hash="none",
        email_verified=True,
    )
    actor = Principal.objects.create(account=account, plane="admin", status="ACTIVE")
    AdminRole.objects.create(principal=actor, role="FULL_ADMIN")
    charger = Charger.objects.create(
        asset_tag="halguard-" + uuid4().hex[:15],
        hal_identity="COW-" + uuid4().hex[:15],
        manufacturer="Synthetic",
        model="30kW",
    )
    connector = ChargerConnector.objects.create(
        charger=charger,
        connector_number=1,
        connector_type="CCS2",
        max_power_w=30000,
        supports_ac=False,
        supports_dc=True,
    )
    return actor, charger, connector


def ack(charger):
    return HALV1Mapping.objects.create(
        charger=charger,
        scope_id=UUID(SCOPE),
        ocpp_identity=charger.hal_identity,
        snapshot=machine.mapping_payload(charger),
        state="SYNCHRONIZED",
    )


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE, COW_HAL_V1_MAPPING_SYNC_ENABLED=True)
def test_unsupported_connector_renumber_keeps_last_acknowledged_snapshot(monkeypatch):
    actor, charger, connector = setup()
    m = ack(charger)
    before = deepcopy(m.snapshot)
    connector.connector_number = 3
    connector.save(update_fields=["connector_number", "updated_at"])
    monkeypatch.setattr(machine, "client", lambda: pytest.fail("Go must not be contacted"))
    with pytest.raises(DomainError) as caught:
        machine.prepare_mapping(actor, charger.pk)
    assert caught.value.code == "HAL_MAPPING_CONNECTOR_CHANGE_UNSUPPORTED"
    m.refresh_from_db()
    assert m.state == "SYNCHRONIZED" and m.snapshot == before


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE, COW_HAL_V1_MAPPING_SYNC_ENABLED=True)
def test_unsupported_ocpp_identity_change_keeps_last_acknowledged_snapshot(monkeypatch):
    actor, charger, connector = setup()
    m = ack(charger)
    old = deepcopy(m.snapshot)
    charger.hal_identity = "COW-REPLACEMENT-" + uuid4().hex[:12]
    charger.save(update_fields=["hal_identity", "updated_at"])
    monkeypatch.setattr(machine, "client", lambda: pytest.fail("Go must not be contacted"))
    with pytest.raises(DomainError) as caught:
        machine.prepare_mapping(actor, charger.pk)
    assert caught.value.code == "HAL_MAPPING_IDENTITY_CHANGE_UNSUPPORTED"
    m.refresh_from_db()
    assert m.state == "SYNCHRONIZED" and m.snapshot == old


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE, COW_HAL_V1_MAPPING_SYNC_ENABLED=True)
def test_physical_gun_unavailable_preserves_logical_mapping():
    actor, charger, connector = setup()
    ack(charger)
    connector.active = False
    connector.save(update_fields=["active", "updated_at"])
    state = machine.prepare_mapping(actor, charger.pk)
    assert state == {
        "charger_id": str(charger.pk),
        "state": "SYNCHRONIZED",
        "already_current": True,
    }


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE, COW_HAL_V1_MAPPING_SYNC_ENABLED=True)
def test_additive_connector_sync_is_local_pending_only():
    actor, charger, connector = setup()
    ack(charger)
    added = ChargerConnector.objects.create(
        charger=charger,
        connector_number=2,
        connector_type="CCS2",
        max_power_w=30000,
        supports_ac=False,
        supports_dc=True,
    )
    result = machine.prepare_mapping(actor, charger.pk)
    assert result["state"] == "PENDING"
    m = HALV1Mapping.objects.get(charger=charger)
    assert m.state == "PENDING"
    assert {c["cms_connector_id"] for c in m.snapshot["connectors"]} == {
        str(connector.pk),
        str(added.pk),
    }


@override_settings(COW_HAL_V1_SCOPE_ID=SCOPE, COW_HAL_V1_ADMIN_OPERATIONS_ENABLED=True)
def test_connector_disabled_after_staging_blocks_operation_dispatch(monkeypatch):
    actor, charger, connector = setup()
    ack(charger)
    receipt = machine.prepare_operation(
        actor,
        charger.pk,
        connector.pk,
        "UNLOCK_CONNECTOR",
        {},
        "guard-test-" + uuid4().hex,
        "test-only maintenance scenario",
    )
    connector.active = False
    connector.save(update_fields=["active", "updated_at"])
    monkeypatch.setattr(machine, "client", lambda: pytest.fail("Go must not be contacted"))
    with pytest.raises(DomainError) as caught:
        machine.send_operation_once(actor, UUID(receipt["id"]))
    assert caught.value.code == "HAL_OPERATION_CONNECTOR_DISABLED"
    op = HALV1ChargerOperation.objects.get(pk=UUID(receipt["id"]))
    assert op.state == "PERSISTED" and op.attempted_at is None
