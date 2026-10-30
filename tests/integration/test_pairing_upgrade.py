"""Actual 0003-to-0004 pairing upgrade assertions for the guarded harness."""

import os

import pytest

from cow.models import VehicleChargerPairing
from scripts.run_pg_tests import (
    UPGRADE_ACTIVE_FROM,
    UPGRADE_ACTIVE_PAIRING_ID,
    UPGRADE_CLOSED_FROM,
    UPGRADE_CLOSED_PAIRING_ID,
    UPGRADE_CLOSED_TO,
)

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def test_0003_pairings_upgrade_without_fabricating_history():
    if os.environ.get("COW_PG_EXPECT_UPGRADE_FIXTURE") != "1":
        pytest.skip("only the guarded 0003-to-0004 rehearsal seeds this fixture")
    pairings = {
        pairing.id: pairing
        for pairing in VehicleChargerPairing.objects.select_related("vehicle", "charger").filter(
            id__in=(UPGRADE_ACTIVE_PAIRING_ID, UPGRADE_CLOSED_PAIRING_ID)
        )
    }
    assert set(pairings) == {UPGRADE_ACTIVE_PAIRING_ID, UPGRADE_CLOSED_PAIRING_ID}

    active = pairings[UPGRADE_ACTIVE_PAIRING_ID]
    assert active.effective_from == UPGRADE_ACTIVE_FROM
    assert active.effective_to is None
    assert active.expected is True
    assert active.status == "ACTIVE"
    assert active.vehicle.asset_tag == "upgrade-active-vehicle"
    assert active.charger.asset_tag == "upgrade-active-charger"

    closed = pairings[UPGRADE_CLOSED_PAIRING_ID]
    assert closed.effective_from == UPGRADE_CLOSED_FROM
    assert closed.effective_to == UPGRADE_CLOSED_TO
    assert closed.expected is False
    assert closed.status == "ENDED"
    assert closed.vehicle.asset_tag == "upgrade-closed-vehicle"
    assert closed.charger.asset_tag == "upgrade-closed-charger"

    for pairing in (active, closed):
        assert pairing.created_at is None
        assert pairing.created_by_id is None
        assert pairing.reason is None
        assert pairing.source is None
        assert pairing.ended_by_id is None
        assert pairing.end_reason is None
