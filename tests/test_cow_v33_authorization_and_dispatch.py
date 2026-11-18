"""Standalone V3.3 security regression checks; no Django/DB/HAL imports.

Hermetically execute the real small auth wrapper and pure START wire predicate
with synthetic dependencies. Integration and physical tests remain separate.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return (ROOT / path).read_text(encoding='utf-8')


def extracted(path, function, bindings):
    tree = ast.parse(source(path), filename=path)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function]
    assert len(nodes) == 1, (path, function)
    fn_module = ast.Module(body=nodes, type_ignores=[])
    ns = dict(bindings)
    exec(compile(ast.fix_missing_locations(fn_module), path, 'exec'), ns)
    return ns[function]


class _Payload:
    def model_dump(self, **kwargs):
        return {'reason': 'the-exact-same-request'}


def auth_harness(*, disabled=False, revoked=False, missing_second=False):
    trace = []

    @contextmanager
    def atomic():
        trace.append('begin')
        try:
            yield
        finally:
            trace.append('end')

    def fresh_admin(actor, capability):
        trace.append(('fresh_admin', capability))
        if revoked:
            raise PermissionError('revoked')
        return actor

    def require(actor, capability):
        trace.append(('extra', capability))
        if missing_second:
            raise PermissionError('extra denied')

    def execute(actor, scope, key, payload, callback, retention_hours):
        trace.append('receipt_lookup')
        assert retention_hours == 168
        return (200, {'cached': True})  # Callback intentionally never runs.

    def reject(code, name, message):
        trace.append(('gate_denied', name))
        raise PermissionError(name)

    kwargs={
        'transaction':SimpleNamespace(atomic=atomic),
        'safety':SimpleNamespace(fresh_admin=fresh_admin),
        'rbac':SimpleNamespace(require=require),
        'idempotency':SimpleNamespace(execute=execute),
        'settings':SimpleNamespace(COW_WALLET_REFUND_POST_ENABLED=not disabled),
        'reject':reject,
    }
    return extracted('cow/admin/nomap_routes.py', '_mutation', kwargs), trace


def test_cached_admin_receipt_denied_after_revocation():
    call, trace = auth_harness(revoked=True)
    try:
        call(SimpleNamespace(auth='admin', headers={'Idempotency-Key':'a'*24}),
            'admin:v3:refund:abc', _Payload(), lambda:None,
            capability='refunds.manage', gate='COW_WALLET_REFUND_POST_ENABLED')
    except PermissionError as exc:
        assert str(exc) == 'revoked'
    else:
        raise AssertionError('revoked Admin accessed cached privileged receipt')
    assert 'receipt_lookup' not in trace
    assert trace == ['begin', ('fresh_admin', 'refunds.manage'), 'end']


def test_cached_admin_receipt_denied_after_gate_reversal():
    call, trace = auth_harness(disabled=True)
    try:
        call(SimpleNamespace(auth='admin', headers={'Idempotency-Key':'a'*24}),
            'admin:v3:refund:abc', _Payload(), lambda:None,
            capability='refunds.manage', gate='COW_WALLET_REFUND_POST_ENABLED')
    except PermissionError as exc:
        assert str(exc) == 'COW_ADMIN_OPERATION_DISABLED'
    else:
        raise AssertionError('disabled release gate accessed receipt')
    assert 'receipt_lookup' not in trace


def test_cached_admin_receipt_requires_every_current_capability():
    call, trace = auth_harness(missing_second=True)
    try:
        call(SimpleNamespace(auth='admin', headers={'Idempotency-Key':'a'*24}),
            'admin:v3:completion:abc', _Payload(), lambda:None,
            capability='jobs.manage', gate='COW_WALLET_REFUND_POST_ENABLED',
            extra_capabilities=('payments.manage',))
    except PermissionError as exc:
        assert str(exc) == 'extra denied'
    else:
        raise AssertionError('cached response bypassed second capability')
    assert 'receipt_lookup' not in trace
    assert trace.index(('fresh_admin','jobs.manage')) < trace.index(('extra','payments.manage'))


def test_authorized_admin_receipt_preserves_idempotency_without_callback():
    call, trace = auth_harness()
    output = call(SimpleNamespace(auth='admin', headers={'Idempotency-Key':'a'*24}),
        'admin:v3:refund:abc', _Payload(),
        lambda: (_ for _ in ()).throw(AssertionError('business callback executed')),
        capability='refunds.manage', gate='COW_WALLET_REFUND_POST_ENABLED')
    assert output == {'cached': True}
    assert trace.index(('fresh_admin','refunds.manage')) < trace.index('receipt_lookup')


def test_all_four_admin_mutations_have_explicit_per_operation_guards():
    text=source('cow/admin/nomap_routes.py')
    assert text.count('capability=') == 4
    assert "capability='dispatch.manage',gate='COW_DRIVER_WORKFLOW_ENABLED'" in text
    assert "capability='jobs.manage',gate='COW_ADMIN_CLOSURE_APPROVAL_ENABLED'" in text
    assert "extra_capabilities=('payments.manage',)" in text
    assert text.count("capability='refunds.manage',gate='COW_WALLET_REFUND_POST_ENABLED'") == 2
    assert "actor=safety.fresh_admin(request.auth,capability)" in text
    wrapper=ast.get_source_segment(text,next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='_mutation'))
    assert wrapper.index('safety.fresh_admin') < wrapper.index('idempotency.execute')


def snapshot_harness():
    permit=SimpleNamespace(
        pk='p1',owner_id='o1',charger_id='c1',connector_id='conn1',
        connector=SimpleNamespace(connector_number=2),limit_type='AUTO',
        energy_limit_wh=12000,energy_limit_source='WALLET',
        max_duration_seconds=0,duration_limit_source='NONE')
    intent=SimpleNamespace(permit_id='p1',mapping_snapshot={'charger_ocpp_identity':'GUN-A'},
        credential_expires_at='t1',command_expires_at='t2')
    cmd=SimpleNamespace(kind='START',intent=intent,intent_id='i1',pk='cmd1',trace_id='trace1')
    cmd.snapshot={
        'trace_id':'trace1','cms_command_id':'cmd1','cms_start_intent_id':'i1',
        'cpo_id':'scope1','customer_id':'o1','cms_charger_id':'c1',
        'cms_connector_id':'conn1','charger_ocpp_identity':'GUN-A',
        'ocpp_connector_number':2,'credential_expires_at':'t1',
        'command_expires_at':'t2','limit_type':'AUTO',
        'energy_limit_wh':12000,'energy_limit_source':'WALLET',
        'max_duration_seconds':0,'duration_limit_source':'NONE',
    }
    fn=extracted('cow/hal/v1_charging.py','_start_snapshot_matches_authority',{
        'v1_machine':SimpleNamespace(scope=lambda:'scope1'),
        '_strtime':lambda value:value,
    })
    return fn,cmd,permit


def test_exact_frozen_command_allowed_and_every_field_guarded():
    fn,cmd,permit=snapshot_harness()
    assert fn(cmd,permit)
    for k,v in list(cmd.snapshot.items()):
        original=cmd.snapshot[k]
        cmd.snapshot[k] = (v+1) if type(v) is int else v+'_TAMPERED'
        assert not fn(cmd,permit),k
        cmd.snapshot[k]=original
        if type(v) is int:
            cmd.snapshot[k]=True
            assert not fn(cmd,permit),k
            cmd.snapshot[k]=original
    cmd.snapshot['unapproved_limit']=1
    assert not fn(cmd,permit)
    del cmd.snapshot['unapproved_limit']
    cmd.intent.permit_id='other'
    assert not fn(cmd,permit)
    cmd.intent.permit_id='p1'
    cmd.intent.mapping_snapshot={}
    assert not fn(cmd,permit)


def test_dispatch_validation_precedes_durable_send_and_stop_is_unchanged():
    s=source('cow/hal/v1_charging.py')
    t=ast.parse(s)
    fn=next(x for x in t.body if isinstance(x,ast.FunctionDef) and x.name=='send_once')
    text=ast.get_source_segment(s,fn)
    assert text.index("_permit_contract(permit,timezone.now(),expected_state='BOUND')") < text.index("cmd.state='DELIVERY_ATTEMPTED'")
    assert text.index('_start_snapshot_matches_authority(cmd,permit)') < text.index("cmd.state='DELIVERY_ATTEMPTED'")
    assert text.index('safety.assert_start_admissible(') < text.index("cmd.state='DELIVERY_ATTEMPTED'")
    assert "'HAL_CHARGE_START_AUTHORITY_DRIFT_BEFORE_SEND'" in text
    assert "COW_BOOKING_COMMERCIAL_START_ENABLED" in text
    assert "COW_BOOKING_CHARGING_STARTS_ENABLED" not in text
    assert "cmd.kind=='START'" in text and "cmd.kind=='STOP'" not in text  # Existing else remains STOP.
    check=ast.get_source_segment(s,next(x for x in t.body if isinstance(x,ast.FunctionDef) and x.name=='_permit_contract'))
    for condition in (
        "permit.state != expected_state", "allocation.state != 'PREPARED'",
        "commercial.get('source') != 'COW_BOOKING_COMMERCIAL_V2'",
        "booking.snapshot_sha256 != stable_digest(booking.snapshot)",
        "commercial.get('tariff') != booking.snapshot.get('selected_tariff')",
        "commercial.get('tax') != booking.snapshot.get('selected_gst')",
        "commercial.get('buffer_paise') != booking.protected_buffer_paise",
        "booking.protected_buffer_paise <= 0",
        "commercial.get('authorized_session_wh') != permit.energy_limit_wh",
        "allocation.budget_gross_paise != commercial.get('budget_gross_paise')",
        "holds > wallet.balance_paise",
    ):
        assert condition in check,condition
