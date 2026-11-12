"""Zero-dependency static contract checks for the C2 vertical implementation."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    data = (ROOT / path).read_text(encoding="utf-8")
    ast.parse(data, filename=path)
    return data


def test_c1_and_stage_b_do_not_lose_their_core_guards():
    c1 = source("cow/services/driver_recruitment.py")
    stage_b = source("cow/services/operational_eligibility.py")
    c2 = source("cow/services/driver_operational.py")
    assert '"eligibility": "PENDING"' in c1
    assert "def assert_start_admissible(" in stage_b
    assert "driver_operational.effective_issues(driver)" in stage_b
    assert "safety.fresh_admin(actor, capability)" in c2
    assert "safety.lock_gate()" in c2
    assert "DRIVER_CLEARANCE_BLOCKED" in c2


def test_routes_are_typed_and_idempotent():
    admin = source("cow/admin/driver_operations.py")
    router = source("cow/admin/router.py")
    driver = source("cow/drivers/router.py")
    assert 'register_driver_operations(router)' in router
    assert 'register_driver_operational(router)' in driver
    for action in ("clear", "suspend", "reinstate", "offboard"):
        assert f'"{action}"' in admin
    assert "wire.version(request)" in admin and "wire.mutate(" in admin
    assert "operation_id=" in admin


def test_history_storage_is_immutable_in_database_and_no_vps_touch():
    migration = source("cow/migrations/0012_driver_operational_c2.py")
    model = source("cow/models.py")
    assert "cow_driver_c2_history_guard" in migration
    assert "refuse_history_loss" in migration
    assert 'class DriverOperationalDecision(models.Model):' in model
    assert "OFFBOARDED" in model
    assert "vps" not in source("cow/services/driver_operational.py").lower()
