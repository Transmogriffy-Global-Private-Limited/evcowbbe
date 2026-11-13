"""Offline static C3 domain/registration/immutability guardrails."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    content = (ROOT / path).read_text(encoding="utf-8")
    ast.parse(content, filename=path)
    return content


def test_c3_single_authoritative_duty_model_and_no_second_online_flag():
    models = read("cow/models.py")
    assert models.count("class DutySession(") == 1
    assert models.count("class DriverSchedule(") == 1
    assert "class DriverDutyEvent(" in models
    assert "class DriverAssetMismatch(" in models
    service = read("cow/services/driver_duty.py")
    assert '"dispatchable": False' in service
    assert "safety.lock_gate()" in service
    assert "clearance.effective_issues(profile)" in service
    assert "_check_no_live_work(duty)" in service
    assert "ServiceJob.objects.filter(driver_duty=duty)" in service
    assert "call_razorpay" not in service and "httpx" not in service


def test_c3_http_routes_are_role_local_and_schemas_shared():
    driver = read("cow/drivers/duty.py")
    admin = read("cow/admin/schedules.py")
    shared = read("cow/common/driver_duty_api.py")
    assert '"/duty/start"' in driver
    assert '"/duty/current/asset-mismatch"' in driver
    assert '"/duty/current/breaks/{break_id}/end"' in driver
    assert '"/schedules"' in admin
    assert '"/duty-sessions/{duty_id}/hold"' in admin
    assert "class DutyView" in shared and "class ScheduleView" in shared
    assert "register_driver_duty(router)" in read("cow/drivers/router.py")
    assert "register_admin_schedules(router)" in read("cow/admin/router.py")


def test_c3_charger_start_rejects_unreconciled_observed_assets():
    safety = read("cow/services/operational_eligibility.py")
    assert "def assert_duty_assets(" in safety
    assert 'DriverAssetMismatch.objects.filter(duty=duty, status="UNRESOLVED")' in safety
    assert '"ASSET_MISMATCH_UNRESOLVED"' in safety


def test_migration_ledger_and_db_history_enforcement_present():
    migration = read("cow/migrations/0015_c3_immutable_duty_history.py")
    assert "BEFORE UPDATE OR DELETE ON cow_driverdutyevent" in migration
    assert '"0014_driver_duty_c3"' in migration
    ledger = read("tests/integration/test_postgres_acceptance.py")
    assert '"0014_driver_duty_c3"' in ledger
    assert '"0015_c3_immutable_duty_history"' in ledger
    assert "assert after == before" in ledger
