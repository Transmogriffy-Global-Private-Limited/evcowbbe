"""HAL machine integration surface and guard contracts (offline, no DB)."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def read(p):return (ROOT/p).read_text(encoding='utf-8')


def test_machine_routes_registered_and_distinct_from_legacy():
    r=read('cow/admin/hal_machine.py')
    for path in ('/hal-v1/chargers/{charger_id}/mapping',
                 '/hal-v1/chargers/{charger_id}/mapping/sync',
                 '/hal-v1/chargers/{charger_id}/mapping/reconcile',
                 '/hal-v1/chargers/{charger_id}/runtime',
                 '/hal-v1/connectors/{connector_id}/runtime',
                 '/hal-v1/chargers/{charger_id}/operations',
                 '/hal-v1/operations/{operation_id}/dispatch',
                 '/hal-v1/operations/{operation_id}/reconcile',
                 '/hal-v1/chargers/{charger_id}/facts'):
        assert path in r
    root=read('cow/api.py')
    assert '/v1/internal/hal-v1/facts' in root
    assert '/v1/internal/hal-v1/trace-events' in root
    assert '/v1/internal/hal/facts' in root
    assert 'register_hal_machine(router)' in read('cow/admin/router.py')


def test_old_hal_is_disabled_when_new_is_configured():
    assert 'LEGACY_HAL_COMMAND_DISABLED' in read('cow/admin/jobs.py')
    assert 'V1_MIGRATION_BLOCKED' in read('cow/hal/client.py')


def test_machine_immutable_receipts_and_no_public_customer_command():
    models=read('cow/models.py')
    for m in ('HALV1Mapping','HALV1ChargerOperation','HALV1FactReceipt',
              'HALV1ChargerRuntime','HALV1ConnectorRuntime','HALV1TraceReceipt'):
        assert 'class '+m+'(' in models
    wire=read('cow/hal/v1_wire.py')
    for path in ('/v1/mappings/chargers/','/v1/remote-commands/start',
                 '/v1/remote-commands/stop','/v1/transactions',
                 '/v1/charger-operations','/v1/facts/'):
        assert path in wire
    machine=read('cow/hal/v1_machine.py')
    assert "COW_HAL_V1_ADMIN_OPERATIONS_ENABLED" in machine
    assert "state='DELIVERY_ATTEMPTED'" in machine
    assert 'def start(' not in read('cow/admin/hal_machine.py')
    assert 'def stop(' not in read('cow/admin/hal_machine.py')


def test_fact_canonicalization_and_scope():
    ingress=read('cow/hal/v1_ingress.py')
    assert 'rfc8785.dumps' in ingress
    assert 'HAL_V1_FOREIGN_SCOPE' in ingress
    assert "return 'PENDING_BUSINESS'" in ingress
    assert 'hmac.compare_digest' in ingress


def test_mapping_reconciliation_is_opt_in_exact_snapshot_only():
    domain=read('cow/hal/v1_machine.py')
    assert 'def reconcile_mapping(' in domain
    assert "m.state not in {'RECONCILIATION_REQUIRED','ATTEMPTED'}" in domain
    assert 'if current!=m.snapshot or m.ocpp_identity!=charger.hal_identity:' in domain
    assert "client().sync_mapping(str(charger_id),frozen,str(uuid4()))" in domain


def test_instance_configuration_fails_closed_if_enabled_without_endpoint():
    settings=read('config/settings.py')
    assert "len(set(_cow_hal_secrets)) != 2" in settings
    assert "and not COW_HAL_V1_BASE_URL" in settings
    assert "COW_HAL_V1_SCOPE_ID" in settings


def test_physical_operation_claims_block_safety_and_duty():
    safety=read('cow/services/operational_eligibility.py')
    assert 'HALV1ChargerOperation' in safety
    assert 'UNRESOLVED_HAL_V1_OPERATION' in safety
    assert 'exclude_hal_v1_operation_id' in safety
    assert 'HAL_V1_MACHINE_BUSY' in safety
    machine=read('cow/hal/v1_machine.py')
    assert 'def cancel_unsent_operation(' in machine
    assert 'def resolve_operation(' in machine
    assert "provider['state']!='OCPP_CONFIRMED'" in machine
