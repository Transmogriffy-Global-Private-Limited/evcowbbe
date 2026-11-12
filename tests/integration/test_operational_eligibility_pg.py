"""Stage B: actual PostgreSQL transaction, RBAC, HAL, and safety guard regressions.

Run only under the existing disposable PostgreSQL acceptance harness.
No remote charger, customer funds, Aiven DB, or physical HAL endpoint used.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import close_old_connections, transaction
from django.test import Client, override_settings
from django.utils import timezone

from cow.common.errors import DomainError
from cow.hal.client import send_operation_once
from cow.models import (
    Account, AdminRole, AuditEvent, Charger, ChargerConnector, ChargingSession,
    DriverProfile, DutySession, HALCommand, Principal, PricingPolicy,
    DriverRecruitmentPolicy, DriverApplication, DriverDocument, DriverDocumentReview,
    Quote, ServiceJob, ServiceRequest, ServiceVehicle, VehicleChargerPairing,
)
from cow.services import auth, fleet
from cow.services import operational_eligibility as admission

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def identity(plane, *, role=None):
    account = Account.objects.create(
        email=f"stage-b-{uuid4().hex}@example.invalid",
        password_hash="not-a-login", email_verified=True,
    )
    p = Principal.objects.create(account=account, plane=plane, status="ACTIVE")
    if role:
        AdminRole.objects.create(principal=p, role=role)
    return p


def setup_operational_job():
    admin = identity("admin", role="FULL_ADMIN")
    driver_principal = identity("driver")
    customer = identity("app")
    today = timezone.localdate(timezone=__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
    driver = DriverProfile.objects.create(
        principal=driver_principal, eligibility="PENDING",
        licence_valid_until=today + timedelta(days=30),
    )
    # Stage B predates C1. Its physical admission fixture now needs genuine
    # synthetic C1 evidence; production must never bypass the new proof guard.
    policy = DriverRecruitmentPolicy.objects.create(
        policy_key="DEFAULT", version=1, required_document_types=["DRIVING_LICENCE"],
        scan_required=False, legal_decision_ref="STAGE_B_SYNTHETIC_FIXTURE",
        active=True, created_by=admin,
    )
    application = DriverApplication.objects.create(
        principal=driver_principal, state="ACTIVE", policy=policy,
    )
    document = DriverDocument.objects.create(
        application=application, document_type="DRIVING_LICENCE",
        storage_key=uuid4().hex, content_type="application/pdf", original_filename="fixture.pdf",
        size_bytes=12, sha256="f" * 64, expires_on=today + timedelta(days=30),
        uploaded_by=driver_principal, review_status="ACCEPTED", scan_status="NOT_SCANNED",
    )
    DriverDocumentReview.objects.create(
        document=document, actor=admin, decision="ACCEPTED", reason="Verified test fixture",
    )
    from cow.services import driver_operational as operational_c2
    operational_c2.decide(admin, driver_principal.pk, 1, "clear",
                          "Verified Stage B synthetic operational clearance")
    vehicle = ServiceVehicle.objects.create(
        asset_tag="SB-V-" + uuid4().hex[:12], make="Fixture", model="EV Van",
        registration_valid_until=today + timedelta(days=30),
        insurance_valid_until=today + timedelta(days=30), inspection_status="VALID",
    )
    charger = Charger.objects.create(
        asset_tag="SB-C-" + uuid4().hex[:12],
        hal_identity="sb-hal-" + uuid4().hex[:12], manufacturer="Fixture",
        model="30kW", rated_power_w=30000,
    )
    connector = ChargerConnector.objects.create(
        charger=charger, connector_number=1, connector_type="CCS2",
        max_power_w=30000, supports_dc=True, supports_ac=False,
    )
    pair = fleet.create_pairing(admin, vehicle.pk, charger.pk, "Reviewed test pairing")
    duty = DutySession.objects.create(
        driver=driver, vehicle=vehicle, charger=charger, state="SERVICING",
        started_at=timezone.now(),
    )
    req = ServiceRequest.objects.create(
        customer=customer, vehicle_snapshot={"model": "Fixture"},
        address_snapshot={"address": "Test-only"}, requested_energy_wh=5000,
        state="ACCEPTED",
    )
    policy = PricingPolicy.objects.create(
        name="SB-" + uuid4().hex[:12], revision=1, status="ACTIVE", currency="INR",
        base_service_paise=0, per_kwh_paise=500,
        effective_from=timezone.now(),
    )
    quote = Quote.objects.create(
        request=req, pricing_policy=policy, pricing_snapshot={"source": "FIXTURE_ONLY"},
        total_paise=3000, expires_at=timezone.now() + timedelta(hours=1),
        accepted_at=timezone.now(),
    )
    job = ServiceJob.objects.create(
        request=req, accepted_quote=quote, driver_duty=duty,
        state="SERVICE_PREPARING",
    )
    return admin, driver_principal, driver, vehicle, charger, connector, pair, duty, job


def test_admission_and_inflight_command_block_conflicting_fleet_changes():
    admin, _, driver, vehicle, charger, _, pair, duty, job = setup_operational_job()
    with transaction.atomic():
        admission.lock_gate()
        assert admission.assert_start_admissible(job.pk, charger.pk, 1).id == job.pk
    client = Client()
    token = auth.make_tokens(admin)["access_token"]
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}",
               "HTTP_IDEMPOTENCY_KEY": "stage-b-start-key-" + uuid4().hex}
    uri = f"/api/v1/admin/jobs/{job.pk}/charging/start"
    body = {"charger_id": str(charger.pk), "connector_number": 1}
    accepted = client.post(uri, json.dumps(body), content_type="application/json", **headers)
    assert accepted.status_code == 202, accepted.content
    replay = client.post(uri, json.dumps(body), content_type="application/json", **headers)
    assert replay.status_code == 202 and replay.json() == accepted.json()
    other = client.post(uri, json.dumps(body), content_type="application/json",
                        **{**headers, "HTTP_IDEMPOTENCY_KEY": "stage-b-second-" + uuid4().hex})
    assert other.status_code == 409, other.content
    assert HALCommand.objects.filter(job=job, action="StartCharging").count() == 1
    with pytest.raises(DomainError) as err:
        fleet.end_pairing(admin, pair.pk, "Must not end live pairing")
    assert err.value.status_code == 409
    with pytest.raises(DomainError):
        fleet.set_charger_active(admin, charger.pk, charger.version, "Cannot shut live charger", False)
    with pytest.raises(DomainError):
        fleet.add_charger_maintenance(admin, charger.pk, timezone.now(),
                                      timezone.now()+timedelta(hours=1), "Cannot strand duty")
    with pytest.raises(DomainError):
        fleet.add_maintenance(admin, vehicle.pk, timezone.now(),
                              timezone.now()+timedelta(hours=1), "Cannot strand duty")
    assert VehicleChargerPairing.objects.get(pk=pair.pk).status == "ACTIVE"
    assert Charger.objects.get(pk=charger.pk).active


def test_invalid_duty_driver_asset_or_connector_never_queues():
    admin, principal, driver, vehicle, charger, _, _, duty, job = setup_operational_job()
    with transaction.atomic():
        admission.lock_gate()
        with pytest.raises(DomainError) as e:
            admission.assert_start_admissible(job.pk, charger.pk, 999)
    assert e.value.code == "CONNECTOR_NOT_OPERATIONAL"
    duty.state = "BREAK"
    duty.save(update_fields=["state", "updated_at"])
    with transaction.atomic():
        admission.lock_gate()
        with pytest.raises(DomainError) as e:
            admission.assert_start_admissible(job.pk, charger.pk, 1)
    assert e.value.code == "DUTY_NOT_ACTIVE"
    duty.state = "SERVICING"
    duty.save(update_fields=["state", "updated_at"])
    principal.status = "SUSPENDED"
    principal.save(update_fields=["status", "updated_at"])
    with transaction.atomic():
        admission.lock_gate()
        with pytest.raises(DomainError) as e:
            admission.assert_start_admissible(job.pk, charger.pk, 1)
    assert e.value.code == "DRIVER_NOT_ELIGIBLE"
    principal.status = "ACTIVE"
    principal.save(update_fields=["status", "updated_at"])
    driver.licence_valid_until = timezone.localdate() - timedelta(days=1)
    driver.save(update_fields=["licence_valid_until", "updated_at"])
    with transaction.atomic():
        admission.lock_gate()
        with pytest.raises(DomainError) as e:
            admission.assert_start_admissible(job.pk, charger.pk, 1)
    assert e.value.code == "DRIVER_NOT_ELIGIBLE"
    assert not HALCommand.objects.filter(job=job).exists()


def test_pending_command_rejected_before_http_when_duty_retracted():
    admin, principal, driver, vehicle, charger, _, _, duty, job = setup_operational_job()
    cmd = HALCommand.objects.create(job=job, charger=charger, action="StartCharging",
                                    params={"connector_number": 1})
    duty.state = "UNAVAILABLE"
    duty.save(update_fields=["state", "updated_at"])
    with override_settings(COW_HAL_BASE_URL="https://invalid.example", COW_HAL_SHARED_SECRET="x" * 40):
        with patch("cow.hal.client.httpx.Client", side_effect=AssertionError("NETWORK MUST NOT BE CALLED")):
            outcome = send_operation_once(cmd.pk)
    assert outcome == "REJECTED"
    cmd.refresh_from_db()
    assert cmd.state == "REJECTED" and cmd.attempted_at is None
    assert AuditEvent.objects.filter(target_type="HALCommand", target_id=cmd.pk,
                                     action="COMMAND_PRESEND_REJECTED").exists()


def test_concurrent_admission_creates_one_start_only():
    admin, _, _, _, charger, _, _, duty, job = setup_operational_job()
    def start(_):
        close_old_connections()
        try:
            with transaction.atomic():
                admission.lock_gate()
                admission.assert_start_admissible(job.pk, charger.pk, 1)
                return HALCommand.objects.create(
                    job=job, charger=charger, action="StartCharging",
                    params={"connector_number": 1},
                ).pk
        except DomainError as exc:
            return exc
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(start, [1, 2]))
    assert sum(not isinstance(x, DomainError) for x in outcomes) == 1
    assert sum(isinstance(x, DomainError) and x.code == "CHARGING_COMMAND_CONFLICT"
               for x in outcomes) == 1
    assert HALCommand.objects.filter(job=job, action="StartCharging").count() == 1


def test_ended_duty_does_not_authorize_new_start():
    _, _, _, _, charger, _, _, duty, job = setup_operational_job()
    duty.ended_at = timezone.now()
    duty.state = "ENDED"
    duty.save(update_fields=["ended_at", "state", "updated_at"])
    with transaction.atomic():
        admission.lock_gate()
        with pytest.raises(DomainError) as err:
            admission.assert_start_admissible(job.pk, charger.pk, 1)
    assert err.value.code == "DUTY_NOT_ACTIVE"


def test_shared_service_rechecks_revoked_admin_capability_inside_transaction():
    from cow.models import AdminOverride
    admin = identity("admin", role="FULL_ADMIN")
    vehicle = ServiceVehicle.objects.create(
        asset_tag="SB-RBAC-" + uuid4().hex[:12], make="Fixture", model="Van")
    AdminOverride.objects.create(
        principal=admin, capability="fleet.manage", decision="DENY")
    with pytest.raises(DomainError) as exc:
        fleet.set_active(admin, vehicle.pk, vehicle.version, "Administrative status change", False)
    assert exc.value.status_code == 403
    vehicle.refresh_from_db()
    assert vehicle.active and vehicle.version == 1


def test_signed_physical_session_remains_blocker_after_duty_ends():
    admin, _, _, vehicle, charger, _, pair, duty, job = setup_operational_job()
    session = ChargingSession.objects.create(
        job=job, charger=charger, connector_number=1,
        hal_transaction_id="sb-haltx-" + uuid4().hex,
        started_at=timezone.now(), state="ACTIVE",
    )
    # Even incorrect direct shutdown of a duty cannot make a physical session
    # disappear. Fleet operations continue to reject until HAL-backed closure.
    duty.ended_at = timezone.now()
    duty.state = "ENDED"
    duty.save(update_fields=["ended_at", "state", "updated_at"])
    with pytest.raises(DomainError) as exc:
        fleet.end_pairing(admin, pair.pk, "Cannot alter physical state by registry change")
    assert exc.value.code == "ASSET_OPERATION_BLOCKED"
    with pytest.raises(DomainError) as exc:
        fleet.set_charger_active(admin, charger.pk, charger.version,
                                 "Cannot deactivate while physically charging", False)
    assert exc.value.code == "ASSET_OPERATION_BLOCKED"
    with pytest.raises(DomainError) as exc:
        fleet.set_active(admin, vehicle.pk, vehicle.version,
                         "Cannot deactivate vehicle while physically charging", False)
    assert exc.value.code == "ASSET_OPERATION_BLOCKED"
    vehicle.refresh_from_db()
    charger.refresh_from_db()
    assert vehicle.active and charger.active
    session.refresh_from_db()
    assert session.state == "ACTIVE"


def test_pairing_only_keeps_previous_deactivation_error_code():
    admin = identity("admin", role="FULL_ADMIN")
    vehicle = ServiceVehicle.objects.create(
        asset_tag="SB-PV-" + uuid4().hex[:12], make="Fixture", model="Van",
    )
    charger = Charger.objects.create(
        asset_tag="SB-PC-" + uuid4().hex[:12],
        manufacturer="Fixture", model="DC",
    )
    fleet.create_pairing(admin, vehicle.pk, charger.pk, "Pairing-only compatibility test")
    with pytest.raises(DomainError) as exc:
        fleet.set_charger_active(admin, charger.pk, charger.version,
                                 "Pairing-only guard", False)
    assert exc.value.status_code == 409 and exc.value.code == "CONFLICT"
    with pytest.raises(DomainError) as exc:
        fleet.set_active(admin, vehicle.pk, vehicle.version,
                         "Pairing-only guard", False)
    assert exc.value.status_code == 409 and exc.value.code == "CONFLICT"
    vehicle.refresh_from_db()
    charger.refresh_from_db()
    assert vehicle.active and charger.active
