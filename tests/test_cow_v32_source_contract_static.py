"""V3.2 source-only shape regression checks (never import Django).

These assertions are intentionally not a substitute for the deferred runtime,
PostgreSQL, API, HAL, payment or physical charging tests.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _function(path, name):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment((ROOT / path).read_text(encoding='utf-8'), node)
    raise AssertionError(f'missing function {path}: {name}')


def test_customer_consent_has_stable_hmac_replay_not_token_rotation():
    s = _function('cow/services/booking_trust_nomap.py', 'customer_consent')
    assert '_consent_token(t)' in s
    assert "if t.state=='CONSENT_READY':" in s
    assert 'hmac.compare_digest(t.consent_token_sha256,_sha(token))' in s
    assert 'HANDOVER_CONSENT_NOT_REPLAYABLE' in s
    assert "'replayed':replayed" in s
    assert 'secrets.token_urlsafe' not in s
    assert "t.duty_id!=duty.pk" in s
    assert '_otp_enabled()' in s
    hmac_src = _function('cow/services/booking_trust_nomap.py', '_consent_token')
    assert "'cow-consent-v2'" in hmac_src
    assert 'ticket.otp_digest' in hmac_src
    assert 'hmac.new(_handover_secret()' in hmac_src


def test_positive_buffer_at_all_admission_layers():
    booking = (ROOT / 'cow/services/booking_commercial.py').read_text(encoding='utf-8')
    hal = _function('cow/hal/v1_charging.py', '_permit_contract')
    assert 'if self.buffer_paise <= 0:' in booking
    assert 'if booking.protected_buffer_paise <= 0:' in booking
    assert 'booking.protected_buffer_paise <= 0 or' in hal
    assert "commercial.get('buffer_paise') != booking.protected_buffer_paise" in hal
    assert 'BOOKING_BUFFER_NOT_APPROVED' in booking


def test_customer_and_admin_use_one_freshness_classifier():
    location = (ROOT / 'cow/services/nomap_location.py').read_text(encoding='utf-8')
    admin = (ROOT / 'cow/admin/nomap_routes.py').read_text(encoding='utf-8')
    assert 'def is_fresh_driver_sample(row, now):' in location
    assert 'if require_fresh and not is_fresh_driver_sample(row,now):' in location
    assert 'stale=not is_fresh_driver_sample(row,now)' in location
    assert 'location.is_fresh_driver_sample(row,now)' in admin
    assert "duty.driver.principal.status!='ACTIVE'" in location
    assert "not getattr(settings,'COW_LOCATION_RECEIVE_ENABLED',False)" in location


def test_typed_additive_customer_consent_response():
    wire = (ROOT / 'cow/common/nomap_wire.py').read_text(encoding='utf-8')
    assert 'class ConsentView(Strict):' in wire
    assert 'replayed: bool = False' in wire
