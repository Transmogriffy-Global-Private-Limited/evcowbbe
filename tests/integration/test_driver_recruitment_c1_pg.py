"""C1 real PostgreSQL lifecycle, file-boundary, RBAC and API acceptance."""
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from cow.common.errors import DomainError
from cow.models import (
    Account, AdminRole, DriverApplication, DriverApplicationHistory,
    DriverDocument, DriverDocumentReview, DriverProfile, DriverRecruitmentPolicy, Principal,
)
from cow.services import auth, driver_recruitment as c1
from cow.services.private_documents import store

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]
REASON = "Verified C1 controlled review decision"


def identity(plane, *, role=None, status=None):
    a = Account.objects.create(email=f"c1-{uuid4().hex}@example.invalid", password_hash="x", email_verified=True)
    p = Principal.objects.create(account=a, plane=plane,
                                 status=status or ("PENDING" if plane == "driver" else "ACTIVE"))
    if role:
        AdminRole.objects.create(principal=p, role=role)
    return p


def active_policy(admin, *, scan_required=False):
    result = c1.create_policy(admin, ["DRIVING_LICENCE", "IDENTITY_PROOF"], scan_required,
                              "LEGAL_DECISION_UNIT_TEST_ONLY", REASON)
    return c1.activate_policy(admin, UUID(result["id"]), REASON)


def put_document(driver, app, kind, *, expires=None, name="document.pdf"):
    uploaded = SimpleUploadedFile(name, b"%PDF-1.7\nhello\n%%EOF", content_type="application/pdf")
    stored = store().put(uploaded)
    result = c1.upload_own(driver, app["version"], kind, name, expires, stored)
    return result["document"], c1.read_own(driver)["application"]


def setup_submitted(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PROVIDER", "LOCAL")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(tmp_path))
    admin = identity("admin", role="FULL_ADMIN")
    driver = identity("driver")
    active_policy(admin)
    app = c1.update_own(driver, 0, "Valid Driver Name", "+919876543210")
    lic, app = put_document(driver, app, "DRIVING_LICENCE", expires=date.today() + timedelta(days=400))
    ident, app = put_document(driver, app, "IDENTITY_PROOF")
    app = c1.submit_own(driver, app["version"])
    return admin, driver, app, lic, ident


def test_nullable_policy_join_can_lock_application_row(monkeypatch, tmp_path):
    """Lock only DriverApplication: nullable policy is an OUTER JOIN in PostgreSQL."""
    from django.db import transaction

    monkeypatch.setenv("STORAGE_PROVIDER", "LOCAL")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(tmp_path))
    driver = identity("driver")
    draft = c1.update_own(driver, 0, "Driver With No Policy", "9999999999")
    with transaction.atomic():
        own = c1._locked_own(driver)
        reviewed = c1._locked_application(UUID(draft["id"]))
        assert own.pk == reviewed.pk
        assert own.policy_id is None and reviewed.policy_id is None


def test_complete_recruitment_separate_from_operational_eligibility(monkeypatch, tmp_path):
    admin, driver, app, licence, identity_doc = setup_submitted(monkeypatch, tmp_path)
    aid = UUID(app["id"])
    app = c1.admin_transition(admin, aid, app["version"], "UNDER_REVIEW", REASON)
    for doc in (licence, identity_doc):
        result = c1.review_document(admin, aid, UUID(doc["id"]), app["version"], "ACCEPTED", REASON)
        app = c1.get_application(admin, aid)["application"]
        assert result["document"]["review_status"] == "ACCEPTED"
    app = c1.admin_transition(admin, aid, app["version"], "APPROVED", REASON)
    assert DriverProfile.objects.filter(principal=driver).count() == 0
    app = c1.admin_transition(admin, aid, app["version"], "ONBOARDING", REASON)
    app = c1.admin_transition(admin, aid, app["version"], "ACTIVE", REASON)
    driver.refresh_from_db()
    profile = DriverProfile.objects.get(principal=driver)
    assert driver.status == "ACTIVE"
    assert profile.eligibility == "PENDING"  # C2 owns operational clearance.
    assert profile.licence_valid_until == date.today() + timedelta(days=400)
    assert DriverApplicationHistory.objects.filter(application_id=aid).count() == 6
    assert DriverDocumentReview.objects.count() == 2
    assert all(x.delivery_state == "NOT_CONFIGURED" for x in DriverApplicationHistory.objects.all())


def test_submission_requires_active_policy_and_document_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PROVIDER", "LOCAL")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(tmp_path))
    driver = identity("driver")
    app = c1.update_own(driver, 0, "Applicant Without Policy", "9999999999")
    with pytest.raises(DomainError) as e:
        c1.submit_own(driver, app["version"])
    assert e.value.code == "DRIVER_POLICY_REQUIRED"
    admin = identity("admin", role="FULL_ADMIN")
    active_policy(admin)
    with pytest.raises(DomainError) as e:
        c1.submit_own(driver, app["version"])
    assert e.value.code == "DRIVER_DOCUMENTS_INCOMPLETE"
    assert c1.read_own(driver)["application"]["state"] == "DRAFT"


def test_request_action_document_replacement_and_old_review_preservation(monkeypatch, tmp_path):
    admin, driver, app, licence, other = setup_submitted(monkeypatch, tmp_path)
    aid = UUID(app["id"])
    app = c1.admin_transition(admin, aid, app["version"], "UNDER_REVIEW", REASON)
    c1.review_document(admin, aid, UUID(licence["id"]), app["version"], "REJECTED", REASON)
    app = c1.get_application(admin, aid)["application"]
    app = c1.admin_transition(admin, aid, app["version"], "ACTION_REQUIRED", REASON)
    replacement, app = put_document(driver, app, "DRIVING_LICENCE", expires=date.today() + timedelta(days=300))
    assert replacement["replaces_id"] == licence["id"]
    app = c1.submit_own(driver, app["version"])
    assert DriverDocumentReview.objects.filter(document_id=UUID(licence["id"])).count() == 1
    assert DriverDocument.objects.filter(application_id=aid, document_type="DRIVING_LICENCE").count() == 2
    assert app["state"] == "SUBMITTED"


def test_scan_requirement_is_release_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PROVIDER", "LOCAL")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(tmp_path))
    admin = identity("admin", role="FULL_ADMIN")
    driver = identity("driver")
    active_policy(admin, scan_required=True)
    app = c1.update_own(driver, 0, "Requires Scanned Documents", "9999999999")
    lic, app = put_document(driver, app, "DRIVING_LICENCE", expires=date.today() + timedelta(days=400))
    _, app = put_document(driver, app, "IDENTITY_PROOF")
    app = c1.submit_own(driver, app["version"])
    app = c1.admin_transition(admin, UUID(app["id"]), app["version"], "UNDER_REVIEW", REASON)
    for doc in DriverDocument.objects.filter(application_id=UUID(app["id"])):
        c1.review_document(admin, UUID(app["id"]), doc.pk, app["version"], "ACCEPTED", REASON)
        app = c1.get_application(admin, UUID(app["id"]))["application"]
    with pytest.raises(DomainError) as exc:
        c1.admin_transition(admin, UUID(app["id"]), app["version"], "APPROVED", REASON)
    assert exc.value.code == "DRIVER_DOCUMENTS_INCOMPLETE"


def test_file_storage_signature_integrity_and_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PROVIDER", "LOCAL")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(tmp_path))
    storage = store()
    with pytest.raises(DomainError) as bad:
        storage.put(SimpleUploadedFile("wrong.pdf", b"MZ executable", content_type="application/pdf"))
    assert bad.value.code == "DOCUMENT_TYPE_REJECTED"
    good = storage.put(SimpleUploadedFile("identity.pdf", b"%PDF-1.7\ntest", content_type="application/pdf"))
    storage.verify(good)
    assert "identity.pdf" not in good.key
    with pytest.raises(DomainError):
        storage.open("../secret")
    storage.delete_uncommitted(good.key)


def test_http_version_idempotency_and_driver_document_idor(monkeypatch, tmp_path):
    admin, driver, app, licence, other = setup_submitted(monkeypatch, tmp_path)
    wrong = identity("driver")
    client = Client()
    authz = {"HTTP_AUTHORIZATION": "Bearer " + auth.make_tokens(driver)["access_token"]}
    other_authz = {"HTTP_AUTHORIZATION": "Bearer " + auth.make_tokens(wrong)["access_token"]}
    response = client.get("/api/v1/driver/application", **authz)
    assert response.status_code == 200, response.content
    assert response.json()["data"]["application"]["state"] == "SUBMITTED"
    wrong_doc = client.get(f"/api/v1/driver/application/documents/{licence['id']}/download", **other_authz)
    assert wrong_doc.status_code == 404, wrong_doc.content
    correct = client.get(f"/api/v1/driver/application/documents/{licence['id']}/download", **authz)
    assert correct.status_code == 200
    assert correct.streaming
    correct.close()
    admin_authz = {"HTTP_AUTHORIZATION": "Bearer " + auth.make_tokens(admin)["access_token"]}
    url = f"/api/v1/admin/driver-applications/{app['id']}/start-review"
    k = "c1-transition-" + uuid4().hex
    h = {**admin_authz, "HTTP_IDEMPOTENCY_KEY": k, "HTTP_IF_MATCH": '"' + str(app["version"]) + '"'}
    a = client.post(url, data='{"reason":"Verified controlled Admin review"}', content_type="application/json", **h)
    assert a.status_code == 200, a.content
    replay = client.post(url, data='{"reason":"Verified controlled Admin review"}', content_type="application/json", **h)
    assert replay.status_code == 200 and replay.json() == a.json()
    stale = client.post(url, data='{"reason":"Verified controlled Admin review"}', content_type="application/json",
        **{**h, "HTTP_IDEMPOTENCY_KEY": "c1-stale-" + uuid4().hex})
    assert stale.status_code == 412, stale.content
    assert DriverApplication.objects.get(pk=UUID(app["id"])).state == "UNDER_REVIEW"


def test_recruitment_evidence_is_database_immutable(monkeypatch, tmp_path):
    from django.db import IntegrityError, transaction
    admin, driver, app, licence, other = setup_submitted(monkeypatch, tmp_path)
    aid = UUID(app["id"])
    app = c1.admin_transition(admin, aid, app["version"], "UNDER_REVIEW", REASON)
    c1.review_document(admin, aid, UUID(licence["id"]), app["version"], "ACCEPTED", REASON)
    history = DriverApplicationHistory.objects.filter(application_id=aid).first()
    review = DriverDocumentReview.objects.get(document_id=UUID(licence["id"]))
    policy = DriverRecruitmentPolicy.objects.get(active=True)
    cases = (
        lambda: DriverApplicationHistory.objects.filter(pk=history.pk).update(reason="tampered"),
        lambda: DriverDocumentReview.objects.filter(pk=review.pk).update(reason="tampered"),
        lambda: DriverDocument.objects.filter(pk=UUID(licence["id"])).update(sha256="f" * 64),
        lambda: DriverRecruitmentPolicy.objects.filter(pk=policy.pk).update(required_document_types=[]),
    )
    for attempt in cases:
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                attempt()
    assert DriverDocument.objects.get(pk=UUID(licence["id"])).sha256 == licence["sha256"]


def test_read_only_admin_cannot_see_private_application(monkeypatch, tmp_path):
    admin, driver, app, licence, other = setup_submitted(monkeypatch, tmp_path)
    readonly = identity("admin", role="READ_ONLY")
    with pytest.raises(DomainError) as denied:
        c1.get_application(readonly, UUID(app["id"]))
    assert denied.value.status_code == 403
    with pytest.raises(DomainError) as denied:
        c1.document_for_download(readonly, UUID(licence["id"]), application_id=UUID(app["id"]))
    assert denied.value.status_code == 403
    assert c1.get_application(admin, UUID(app["id"]))["application"]["state"] == "SUBMITTED"


def test_concurrent_distinct_admin_decisions_have_one_winner(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    admin, driver, app, licence, other = setup_submitted(monkeypatch, tmp_path)
    aid = UUID(app["id"])
    app = c1.admin_transition(admin, aid, app["version"], "UNDER_REVIEW", REASON)
    original_version = app["version"]

    def decide(target):
        close_old_connections()
        try:
            return c1.admin_transition(admin, aid, original_version, target, REASON)
        except Exception as error:
            return error
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ["ACTION_REQUIRED", "REJECTED"]))
    success = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, DomainError)]
    assert len(success) == 1 and len(failures) == 1
    assert failures[0].code == "RESOURCE_VERSION_MISMATCH"
    assert DriverApplication.objects.get(pk=aid).state == success[0]["state"]
