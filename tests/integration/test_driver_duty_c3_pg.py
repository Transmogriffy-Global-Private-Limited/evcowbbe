"""C3 transactional PostgreSQL acceptance. NEVER run on Aiven DEV/production.

The guarded acceptance harness must positively identify disposable PostgreSQL
before collecting or running any test in tests/integration/.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client
from django.utils import timezone

from cow.common.errors import DomainError
from cow.models import (
    Account, AdminOverride, AdminRole, AuditEvent, Charger, ChargerConnector,
    DriverApplication, DriverDocument, DriverDocumentReview,
    DriverDutyBreak, DriverDutyEvent, DriverAssetMismatch, DriverProfile,
    DriverRecruitmentPolicy, DriverSchedule, DutySession, Principal,
    PricingPolicy, Quote, ServiceJob, ServiceRequest, ServiceVehicle,
)
from cow.services import auth, driver_duty as c3, driver_operational as c2, fleet

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def identity(plane, *, role=None):
    account = Account.objects.create(
        email=f"c3-{uuid4().hex}@example.invalid",
        password_hash="not-a-login", email_verified=True,
    )
    principal = Principal.objects.create(account=account, plane=plane, status="ACTIVE")
    if role:
        AdminRole.objects.create(principal=principal, role=role)
    return principal


def fixture():
    admin = identity("admin", role="FULL_ADMIN")
    driver = identity("driver")
    today = c2.business_today()
    expiry = today + timedelta(days=30)
    policy = DriverRecruitmentPolicy.objects.create(
        policy_key="DEFAULT", version=1,
        required_document_types=["DRIVING_LICENCE"], scan_required=False,
        legal_decision_ref="C3_SYNTHETIC_FIXTURE_ONLY", active=True, created_by=admin,
    )
    app = DriverApplication.objects.create(principal=driver, state="ACTIVE", policy=policy)
    doc = DriverDocument.objects.create(
        application=app, document_type="DRIVING_LICENCE",
        storage_key=uuid4().hex, content_type="application/pdf",
        original_filename="test.pdf", size_bytes=5, sha256="f" * 64,
        expires_on=expiry, uploaded_by=driver, review_status="ACCEPTED",
        scan_status="NOT_SCANNED",
    )
    DriverDocumentReview.objects.create(
        document=doc, actor=admin, decision="ACCEPTED", reason="C3 synthetic reviewed evidence",
    )
    profile = DriverProfile.objects.create(principal=driver, eligibility="PENDING",
                                           licence_valid_until=expiry)
    c2.decide(admin, driver.pk, 1, "clear", "C3 controlled operational clearance")
    vehicle = ServiceVehicle.objects.create(
        asset_tag="C3-V-" + uuid4().hex[:12], make="Fixture", model="Van",
        registration_valid_until=expiry, insurance_valid_until=expiry,
        inspection_status="VALID",
    )
    charger = Charger.objects.create(
        asset_tag="C3-C-" + uuid4().hex[:12],
        hal_identity="c3-hal-" + uuid4().hex[:12],
        manufacturer="Fixture", model="30kW", rated_power_w=30000,
    )
    ChargerConnector.objects.create(
        charger=charger, connector_number=1, connector_type="CCS2",
        max_power_w=30000, supports_ac=False, supports_dc=True,
    )
    fleet.create_pairing(admin, vehicle.pk, charger.pk, "C3 controlled asset pairing")
    now = timezone.now()
    slot = c3.create_schedule(admin, {
        "driver_id": driver.pk, "vehicle_id": vehicle.pk, "charger_id": charger.pk,
        "starts_at": now - timedelta(minutes=2),
        "ends_at": now + timedelta(hours=3),
    })
    return admin, driver, profile, vehicle, charger, slot


def test_schedule_to_duty_break_resume_end_and_immutable_history():
    admin, driver, profile, vehicle, charger, slot = fixture()
    started = c3.start_duty(driver, slot["id"])
    assert started["state"] == "AVAILABLE" and started["version"] == 1
    assert started["dispatchable"] is False
    assert "LOCATION_NOT_VERIFIED" in started["unavailable_reasons"]
    assert c3.current_duty(driver)["id"] == started["id"]
    with pytest.raises(DomainError) as already:
        c3.start_duty(driver, slot["id"])
    assert already.value.code == "DUTY_ALREADY_ACTIVE"

    brk = c3.start_break(driver, 1, "Driver requires a rest break")
    assert brk["duty"]["state"] == "ON_BREAK"
    with pytest.raises(DomainError) as stale:
        c3.end_duty(driver, 1, "Old version cannot close duty")
    assert stale.value.status_code == 412
    resumed = c3.end_break(driver, brk["break_id"], 2)
    assert resumed["state"] == "AVAILABLE" and resumed["version"] == 3
    ended = c3.end_duty(driver, 3, "Scheduled assignment is complete")
    assert ended["state"] == "OFF_DUTY" and ended["ended_at"]
    assert c3.current_duty(driver) is None
    assert DriverDutyBreak.objects.filter(duty_id=started["id"], ended_at__isnull=True).count() == 0
    assert list(DriverDutyEvent.objects.filter(duty_id=started["id"]).order_by(
        "result_version").values_list("action", flat=True)) == [
        "START", "BREAK_START", "BREAK_END", "END",
    ]
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DriverDutyEvent.objects.filter(duty_id=started["id"]).update(reason="Tampered")
    assert DutySession.objects.get(pk=started["id"]).version == 4
    with pytest.raises(DomainError) as reused:
        c3.start_duty(driver, slot["id"])
    assert reused.value.code == "SCHEDULE_ALREADY_USED"


def test_schedule_collision_conflicting_assets_and_owner_idor():
    admin, driver, profile, vehicle, charger, slot = fixture()
    another_driver = identity("driver")
    now = timezone.now()
    with pytest.raises(DomainError) as overlap:
        c3.create_schedule(admin, {
            "driver_id": driver.pk, "vehicle_id": vehicle.pk,
            "charger_id": charger.pk, "starts_at": now,
            "ends_at": now + timedelta(minutes=40),
        })
    assert overlap.value.code == "SCHEDULE_OVERLAP"
    with pytest.raises(DomainError) as foreign:
        c3.start_duty(another_driver, slot["id"])
    assert foreign.value.code in {"DRIVER_PROFILE_MISSING", "SCHEDULE_NOT_FOUND"}
    with pytest.raises(DomainError) as private:
        c3.get_own_schedule(another_driver, slot["id"])
    assert private.value.status_code == 404
    assert c3.get_own_schedule(driver, slot["id"])["id"] == slot["id"]


def test_cannot_cancel_or_amend_started_occurrence():
    admin, driver, profile, vehicle, charger, slot = fixture()
    changed = c3.amend_schedule(admin, slot["id"], 1, {
        "reason": "Minor shift adjustment authorized",
        "ends_at": timezone.now() + timedelta(hours=4),
    })
    assert changed["version"] == 2
    with pytest.raises(DomainError) as stale:
        c3.cancel_schedule(admin, slot["id"], 1, "Stale cancellation version")
    assert stale.value.status_code == 412
    c3.start_duty(driver, slot["id"])
    with pytest.raises(DomainError) as used:
        c3.cancel_schedule(admin, slot["id"], 2, "Started occurrence cannot be edited")
    assert used.value.code == "SCHEDULE_IMMUTABLE"


def test_admin_hold_requires_permissions_and_releases_without_duplicating_claim():
    admin, driver, profile, vehicle, charger, slot = fixture()
    started = c3.start_duty(driver, slot["id"])
    held = c3.admin_hold(admin, started["id"], 1, "Temporary operational safety review")
    assert held["state"] == "HELD"
    with pytest.raises(DomainError) as blocked:
        c3.start_break(driver, 2, "Cannot break while administratively held")
    assert blocked.value.code == "DUTY_STATE_CONFLICT"
    release = c3.admin_hold(admin, started["id"], 2,
                            "Operations completed safety review", release=True)
    assert release["state"] == "AVAILABLE" and release["version"] == 3
    assert DutySession.objects.filter(vehicle=vehicle, ended_at__isnull=True).count() == 1
    AdminOverride.objects.create(principal=admin, capability="schedules.manage", decision="DENY")
    with pytest.raises(DomainError) as denied:
        c3.admin_hold(admin, started["id"], 3, "Denied admin operation attempt")
    assert denied.value.status_code == 403


def test_asset_mismatch_is_durable_and_blocks_new_availability():
    admin, driver, profile, vehicle, charger, slot = fixture()
    duty = c3.start_duty(driver, slot["id"])
    result = c3.report_asset_mismatch(
        driver, 1, uuid4(), uuid4(), "Observed physical assets are different",
    )
    assert result["duty"]["state"] == "HELD"
    assert not result["duty"]["dispatchable"]
    assert DriverAssetMismatch.objects.filter(duty_id=duty["id"],
                                              status="UNRESOLVED").count() == 1
    with pytest.raises(DomainError) as blocked:
        c3.admin_hold(admin, duty["id"], 2, "Unsafe to release unresolved assets", release=True)
    assert blocked.value.code == "ASSET_MISMATCH_UNRESOLVED"
    ended = c3.end_duty(driver, 2, "Ending duty for incident investigation")
    assert ended["ended_at"]
    assert DriverAssetMismatch.objects.filter(duty_id=duty["id"]).count() == 1
    # Ending a duty must not erase a physical safety hold on shared assets.
    with pytest.raises(DomainError) as new_claim:
        c3.create_schedule(admin, {
            "driver_id": driver.pk, "vehicle_id": vehicle.pk,
            "charger_id": charger.pk, "starts_at": timezone.now(),
            "ends_at": timezone.now() + timedelta(hours=1),
        })
    assert new_claim.value.code == "ASSET_MISMATCH_UNRESOLVED"


def test_preexisting_live_job_blocks_duty_end_without_closing_anything():
    admin, driver, profile, vehicle, charger, slot = fixture()
    duty_info = c3.start_duty(driver, slot["id"])
    customer = identity("app")
    request = ServiceRequest.objects.create(
        customer=customer, vehicle_snapshot={"model": "Fixture"},
        address_snapshot={"address": "Test only"}, requested_energy_wh=5000,
        state="ACCEPTED",
    )
    policy = PricingPolicy.objects.create(
        name="C3-" + uuid4().hex[:12], revision=1, status="ACTIVE", currency="INR",
        base_service_paise=0, per_kwh_paise=500, effective_from=timezone.now(),
    )
    quote = Quote.objects.create(
        request=request, pricing_policy=policy, pricing_snapshot={"source": "test"},
        total_paise=3000, expires_at=timezone.now() + timedelta(hours=1),
        accepted_at=timezone.now(),
    )
    job = ServiceJob.objects.create(
        request=request, accepted_quote=quote, driver_duty_id=duty_info["id"],
        state="SERVICE_PREPARING",
    )
    with pytest.raises(DomainError) as blocked:
        c3.end_duty(driver, 1, "Must not abandon an active job")
    assert blocked.value.code == "DUTY_WORK_UNRESOLVED"
    assert DutySession.objects.get(pk=duty_info["id"]).ended_at is None
    assert ServiceJob.objects.get(pk=job.pk).state == "SERVICE_PREPARING"


def test_c3_driver_http_idempotency_cas_and_read_envelope():
    admin, driver, profile, vehicle, charger, slot = fixture()
    admin_client = Client()
    driver_client = Client()
    admin_token = auth.make_tokens(admin)["access_token"]
    driver_token = auth.make_tokens(driver)["access_token"]
    start_headers = {
        "HTTP_AUTHORIZATION": "Bearer " + driver_token,
        "HTTP_IDEMPOTENCY_KEY": "c3-start-" + uuid4().hex,
    }
    url = "/api/v1/driver/duty/start"
    data = '{"schedule_id":"' + slot["id"] + '"}'
    resp = driver_client.post(url, data, content_type="application/json", **start_headers)
    assert resp.status_code == 201, resp.content
    repeat = driver_client.post(url, data, content_type="application/json", **start_headers)
    assert repeat.status_code == 201 and repeat.json() == resp.json()
    assert DriverDutyEvent.objects.filter(duty_id=resp.json()["data"]["id"]).count() == 1
    current = driver_client.get("/api/v1/driver/duty/current",
                                HTTP_AUTHORIZATION="Bearer " + driver_token)
    assert current.status_code == 200 and current.json()["data"]["version"] == 1
    break_headers = {
        **start_headers, "HTTP_IDEMPOTENCY_KEY": "c3-break-" + uuid4().hex,
        "HTTP_IF_MATCH": '"0"',
    }
    stale = driver_client.post(
        "/api/v1/driver/duty/current/breaks",
        '{"reason":"A properly described rest break"}',
        content_type="application/json", **break_headers,
    )
    assert stale.status_code == 412
    unauthorized = driver_client.post(
        "/api/v1/driver/duty/current/end",
        '{"reason":"Not authorized to end a driver duty"}',
        content_type="application/json", HTTP_AUTHORIZATION="Bearer " + admin_token,
        HTTP_IDEMPOTENCY_KEY="c3-cross-plane-" + uuid4().hex,
        HTTP_IF_MATCH='"1"',
    )
    assert unauthorized.status_code == 403


def test_two_real_postgres_connections_compete_for_one_duty():
    admin, driver, profile, vehicle, charger, slot = fixture()

    def try_start():
        close_old_connections()
        try:
            return c3.start_duty(driver, slot["id"])
        except Exception as error:
            return error
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [f.result(timeout=30) for f in (pool.submit(try_start),
                                                     pool.submit(try_start))]
    success = [x for x in outcomes if isinstance(x, dict)]
    rejected = [x for x in outcomes if isinstance(x, DomainError)]
    assert len(success) == 1 and len(rejected) == 1, outcomes
    assert rejected[0].code == "DUTY_ALREADY_ACTIVE"
    assert DutySession.objects.filter(driver=profile, ended_at__isnull=True).count() == 1
    assert DriverDutyEvent.objects.filter(duty_id=success[0]["id"]).count() == 1
