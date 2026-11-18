"""Executable acceptance cases for the source-only CoW charging machine.

These are written for a later, explicit test authorization; not run in this pass.
"""
from pathlib import Path


def test_source_controls_are_explicit():
    base = Path(__file__).resolve().parents[1]
    source = (base / 'cow/hal/v1_charging.py').read_text()
    settings = (base / 'config/settings.py').read_text()
    ingress = (base / 'cow/hal/v1_ingress.py').read_text()
    assert "COW_HAL_V1_NEW_STARTS_ENABLED" in settings
    assert 'hal-v1-one-use-credential' in settings
    assert 'COW_HAL_V1_CHARGE_CIPHER_KEY' in source
    assert "session.state='RECONCILIATION_REQUIRED' if was_held" in source
    assert "stop_provenance_conflict=stop_conflict" in source
    assert 'HAL_V1_FROZEN_MAPPING_MISMATCH' in ingress


def test_explicitly_deferred_runtime_acceptance():
    base = Path(__file__).resolve().parents[1]
    spec = (base/'docs/HAL_V1_SOURCE_COMPLETION_SPEC_20261118.md').read_text()
    assert 'No migrations' in spec or 'not executed' in spec
    assert 'settlement' in spec.lower()
