"""C2 real PostgreSQL operational lifecycle, acceptance and concurrency.

Only run under the repository's explicitly approved, disposable PostgreSQL test
harness. It never connects to the Aiven dev DB or real hardware/providers.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client
from django.utils import timezone

from cow.common.errors import DomainError
from cow.models import (
    Account, AdminOverride, AdminRole, Charger, DriverApplication,
    DriverDocument, DriverDocumentReview, DriverOperationalDecision,
    DriverProfile, DriverRecruitmentPolicy, DutySession, Principal, ServiceVehicle,
)
from cow.services import auth, driver_operational as c2

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]
REASON = "Controlled operational clearance decision"


def identity(plane, *, role=None, status="ACTIVE"):
    acct = Account.objects.create(email=f"c2-{uuid4().hex}@example.invalid",
                                  password_hash="irrelevant", email_verified=True)
    principal = Principal.objects.create(account=acct, plane=plane, status=status)
    if role:
        AdminRole.objects.create(principal=principal, role=role)
    return principal


def setup_ready(*, scan_required=False, scan_status=None, licence_days=45):
    admin = identity("admin", role="FULL_ADMIN")
    driver = identity("driver")
    today = c2.business_today()
    policy = DriverRecruitmentPolicy.objects.create(
        policy_key="DEFAULT", version=1, required_document_types=["DRIVING_LICENCE", "IDENTITY_PROOF"],
        scan_required=scan_required, legal_decision_ref="LEGAL_REVIEW_TEST_ONLY",
        active=True, created_by=admin,
    )
    app = DriverApplication.objects.create(principal=driver, state="ACTIVE", policy=policy,
                                           legal_name="C2 synthetic", contact_phone="9999999999")
    expiry = today + timedelta(days=licence_days)
    for kind in policy.required_document_types:
        doc = DriverDocument.objects.create(
            application=app, document_type=kind, storage_key=uuid4().hex,
            content_type="application/pdf", original_filename="test.pdf", size_bytes=5,
            sha256="f" * 64, expires_on=expiry if kind == "DRIVING_LICENCE" else None,
            uploaded_by=driver, review_status="ACCEPTED",
            scan_status=scan_status or ("CLEAN" if scan_required else "NOT_SCANNED"),
        )
        DriverDocumentReview.objects.create(document=doc, actor=admin, decision="ACCEPTED",
                                            reason=REASON)
    profile = DriverProfile.objects.create(principal=driver, eligibility="PENDING",
                                           licence_valid_until=expiry)
    return admin, driver, profile, app


def test_insert_active_profile_without_clearance_is_database_rejected():
    # A direct INSERT cannot bypass C2 decision/audit authority.
    driver = identity("driver")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DriverProfile.objects.create(principal=driver, eligibility="ACTIVE")
    assert not DriverProfile.objects.filter(principal=driver).exists()


def test_clear_suspend_reinstate_offboard_preserve_c1_history():
    admin, driver, profile, app = setup_ready()
    assert c2.effective_issues(profile) == ["OPERATIONAL_PENDING"]
    cleared = c2.decide(admin, driver.pk, 1, "clear", REASON)
    assert cleared["effective_eligible"] is True and cleared["version"] == 2
    suspended = c2.decide(admin, driver.pk, 2, "suspend", REASON)
    assert suspended["eligibility"] == "SUSPENDED" and not suspended["effective_eligible"]
    reinstated = c2.decide(admin, driver.pk, 3, "reinstate", REASON)
    assert reinstated["effective_eligible"] is True
    offboarded = c2.decide(admin, driver.pk, 4, "offboard", REASON)
    assert offboarded["eligibility"] == "OFFBOARDED" and offboarded["version"] == 5
    profile.refresh_from_db()
    app.refresh_from_db()
    assert profile.eligibility == "OFFBOARDED" and app.state == "ACTIVE"
    assert list(DriverOperationalDecision.objects.filter(driver=profile).order_by("occurred_at")
                .values_list("action", flat=True)) == ["CLEAR", "SUSPEND", "REINSTATE", "OFFBOARD"]
    with pytest.raises(DomainError) as no_reversal:
        c2.decide(admin, driver.pk, 5, "reinstate", REASON)
    assert no_reversal.value.code == "DRIVER_OPERATIONAL_STATE_CONFLICT"


def test_scan_and_expiration_fail_closed_at_local_day_boundary():
    admin, driver, profile, app = setup_ready(scan_required=True, scan_status="NOT_SCANNED")
    with pytest.raises(DomainError) as exc:
        c2.decide(admin, driver.pk, 1, "clear", REASON)
    assert exc.value.code == "DRIVER_CLEARANCE_BLOCKED"
    assert "REQUIRED_DOCUMENT_NOT_SCANNED" in str(exc.value)
    profile.refresh_from_db()
    assert profile.eligibility == "PENDING" and profile.version == 1
    # Pure date-only evaluation stays valid through the recorded calendar date.
    assert "DRIVING_LICENCE_INVALID" not in c2.evidence_issues(
        profile, today=profile.licence_valid_until
    )
    assert "DRIVING_LICENCE_INVALID" in c2.evidence_issues(
        profile, today=profile.licence_valid_until + timedelta(days=1)
    )


def test_normal_mutation_refuses_open_duty_without_stranding_claims():
    admin, driver, profile, app = setup_ready()
    c2.decide(admin, driver.pk, 1, "clear", REASON)
    vehicle = ServiceVehicle.objects.create(asset_tag="C2-V-" + uuid4().hex[:14],
                                            make="Fixture", model="Mobile Unit")
    charger = Charger.objects.create(asset_tag="C2-C-" + uuid4().hex[:14],
                                     manufacturer="Fixture", model="Charger")
    duty = DutySession.objects.create(driver=profile, vehicle=vehicle, charger=charger,
                                      state="SERVICING", started_at=timezone.now())
    with pytest.raises(DomainError) as exc:
        c2.decide(admin, driver.pk, 2, "suspend", REASON)
    assert exc.value.code == "DRIVER_OPERATION_BLOCKED"
    assert "OPEN_DRIVER_DUTY" in str(exc.value)
    duty.refresh_from_db()
    profile.refresh_from_db()
    assert duty.ended_at is None and profile.eligibility == "ACTIVE"
    assert DriverOperationalDecision.objects.filter(driver=profile).count() == 1


def test_http_idempotency_cas_and_plane_permissions():
    admin, driver, profile, app = setup_ready()
    client = Client()
    token = auth.make_tokens(admin)["access_token"]
    uri = f"/api/v1/admin/drivers/{driver.pk}/clear"
    headers = {"HTTP_AUTHORIZATION": "Bearer " + token,
               "HTTP_IDEMPOTENCY_KEY": "c2-clear-" + uuid4().hex,
               "HTTP_IF_MATCH": '"1"'}
    body = '{"reason":"Controlled operational clearance decision"}'
    resp = client.post(uri, body, content_type="application/json", **headers)
    assert resp.status_code == 200, resp.content
    assert resp.json()["data"]["effective_eligible"]
    retry = client.post(uri, body, content_type="application/json", **headers)
    assert retry.status_code == 200 and retry.json() == resp.json()
    assert DriverOperationalDecision.objects.filter(driver=profile).count() == 1
    changed = client.post(uri, '{"reason":"A different reason for same key"}',
                          content_type="application/json", **headers)
    assert changed.status_code == 409
    stale = client.post(uri, body, content_type="application/json", **{
        **headers, "HTTP_IDEMPOTENCY_KEY": "c2-stale-" + uuid4().hex,
    })
    assert stale.status_code == 412
    unauthorized = identity("admin", role="READ_ONLY")
    bad = client.post(uri, body, content_type="application/json", **{
        **headers, "HTTP_IDEMPOTENCY_KEY": "c2-denied-" + uuid4().hex,
        "HTTP_AUTHORIZATION": "Bearer " + auth.make_tokens(unauthorized)["access_token"],
        "HTTP_IF_MATCH": '"2"',
    })
    assert bad.status_code == 403
    AdminOverride.objects.create(principal=admin, capability="drivers.suspend", decision="DENY")
    with pytest.raises(DomainError) as denied:
        c2.decide(admin, driver.pk, 2, "suspend", REASON)
    assert denied.value.code == "CAPABILITY_DENIED"
    self_token = auth.make_tokens(driver)["access_token"]
    own = client.get("/api/v1/driver/operational", HTTP_AUTHORIZATION="Bearer " + self_token)
    assert own.status_code == 200 and own.json()["data"]["eligibility"] == "ACTIVE"


def test_concurrent_different_decisions_one_winner_one_stale():
    admin, driver, profile, app = setup_ready()
    c2.decide(admin, driver.pk, 1, "clear", REASON)

    def run(action):
        close_old_connections()
        try:
            return c2.decide(admin, driver.pk, 2, action, REASON)
        except Exception as error:
            return error
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("suspend", "offboard")))
    assert sum(isinstance(x, dict) for x in results) == 1
    assert sum(isinstance(x, DomainError) and x.code == "RESOURCE_VERSION_MISMATCH"
               for x in results) == 1
    assert DriverOperationalDecision.objects.filter(driver=profile).count() == 2


def test_db_history_cannot_be_rewritten_or_deleted():
    admin, driver, profile, app = setup_ready()
    c2.decide(admin, driver.pk, 1, "clear", REASON)
    decision = DriverOperationalDecision.objects.get(driver=profile)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DriverOperationalDecision.objects.filter(pk=decision.pk).update(reason="tamper")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DriverOperationalDecision.objects.filter(pk=decision.pk).delete()
    assert DriverOperationalDecision.objects.filter(pk=decision.pk).count() == 1


def test_stage_b_start_admission_requires_current_c1_evidence():
    # Reuse the already-verified Stage B physical fixture, now upgraded to
    # carry explicit synthetic C1 evidence. This test is not a HAL invocation.
    from cow.services import operational_eligibility as admission
    from tests.integration.test_operational_eligibility_pg import setup_operational_job

    _, _, profile, _, charger, _, _, _, job = setup_operational_job()
    with transaction.atomic():
        admission.lock_gate()
        assert admission.assert_start_admissible(job.pk, charger.pk, 1).pk == job.pk
    # A direct status rewrite is rejected by C2's deferred database guard.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            profile.eligibility = "OFFBOARDED"
            profile.save(update_fields=["eligibility", "updated_at"])
    profile.refresh_from_db()
    assert profile.eligibility == "ACTIVE"
    # Credential expiry still rejects new physical starts without rewriting
    # the historical operational approval decision.
    profile.licence_valid_until = c2.business_today() - timedelta(days=1)
    profile.save(update_fields=["licence_valid_until", "updated_at"])
    with transaction.atomic():
        admission.lock_gate()
        with pytest.raises(DomainError) as exc:
            admission.assert_start_admissible(job.pk, charger.pk, 1)
    assert exc.value.code == "DRIVER_NOT_ELIGIBLE"


def test_private_decision_history_is_bounded_and_role_scoped():
    admin, driver, profile, app = setup_ready()
    c2.decide(admin, driver.pk, 1, "clear", REASON)
    c2.decide(admin, driver.pk, 2, "suspend", REASON)
    page = c2.read_decisions(admin, driver.pk, limit=1)
    assert len(page["items"]) == 1
    assert page["next_before_at"] is not None and page["next_before_id"]
    from datetime import datetime
    following = c2.read_decisions(admin, driver.pk, limit=1,
        before_at=datetime.fromisoformat(page["next_before_at"]),
        before_id=UUID(page["next_before_id"]))
    assert len(following["items"]) == 1
    assert following["items"][0]["id"] != page["items"][0]["id"]
    readonly = identity("admin", role="READ_ONLY")
    with pytest.raises(DomainError) as exc:
        c2.read_decisions(readonly, driver.pk)
    assert exc.value.code == "CAPABILITY_DENIED"
