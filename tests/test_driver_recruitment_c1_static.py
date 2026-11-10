"""Fast contract assertions for the Driver C1 vertical slice."""
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def test_recruitment_routes_mounted_under_existing_plane_bearers():
    driver = (BASE / "cow/drivers/router.py").read_text()
    admin = (BASE / "cow/admin/router.py").read_text()
    assert "register_driver_applications(router)" in driver
    assert "register_driver_applications(router)" in admin
    assert driver.index('mount_identity(router, "driver")') < driver.index('register_driver_applications(router)')
    assert admin.index('register_admin_management(router)') < admin.index('register_driver_applications(router)')


def test_no_implicit_dispatch_after_onboarding():
    domain = (BASE / "cow/services/driver_recruitment.py").read_text()
    assert '"eligibility": "PENDING"' in domain
    assert 'safety.fresh_admin(actor, capability)' in domain
    assert 'safety.lock_gate()' in domain
    assert '"DRIVER_DOCUMENTS_INCOMPLETE"' in domain


def test_private_storage_path_is_server_owned_and_download_is_attachment():
    storage = (BASE / "cow/services/private_documents.py").read_text()
    driver = (BASE / "cow/drivers/application.py").read_text()
    admin = (BASE / "cow/admin/driver_applications.py").read_text()
    assert 'secrets.token_hex(16)' in storage
    assert 'PRIVATE_STORAGE_ROOT' in storage
    assert 'FileResponse(stream, as_attachment=True' in driver
    assert 'FileResponse(stream, as_attachment=True' in admin
    assert '"X-Document-SHA256"' in driver


def test_migration_append_only_history_and_review_evidence():
    migration = (BASE / "cow/migrations/0011_driver_recruitment_c1.py").read_text()
    assert 'cow_driver_history_append_only' in migration
    assert 'cow_driver_review_append_only' in migration
    assert 'cow_driver_doc_immutable' in migration
    assert 'cow_driver_policy_immutable' in migration


def test_live_openapi_has_source_implemented_driver_and_admin_contracts():
    from cow.api import api
    schema = api.get_openapi_schema()
    paths = schema["paths"]
    assert "/api/v1/driver/application" in paths
    assert "/api/v1/driver/application/documents" in paths
    assert "/api/v1/admin/driver-applications" in paths
    assert "/api/v1/admin/driver-application-policies" in paths
    upload = paths["/api/v1/driver/application/documents"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
    for path in ("/api/v1/driver/application", "/api/v1/admin/driver-applications"):
        assert paths[path]["get"].get("security")
    assert schema["x-fe-contract-coverage"]["operations"] >= 85
