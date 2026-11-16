"""Offline, pure mapping-transition tests: no Django DB or Go HAL required."""

import copy
import unittest

from cow.hal.v1_mapping_evolution import (
    UnsupportedMappingEvolution,
    verify_additive_mapping_change,
)


SCOPE = "f507864d-f5b9-4c6c-9503-0e4b6e72a441"
CHARGER = "1064e70a-9727-4237-8354-26d86e7f9f27"
CONN1 = "213830d0-950a-4118-b318-3acf63a8a98e"
CONN2 = "d261e946-8cae-4ad1-a4d1-dcc5155d53a2"
OTHER = "fed7a722-2b36-4531-a725-b68a45b124d4"


def sample():
    return {
        "cpo_id": SCOPE,
        "cms_charger_id": CHARGER,
        "charger_ocpp_identity": "COW-CHARGER-1",
        "expected_serial": "SERIAL-A",
        "enabled": True,
        "connectors": [
            {"cms_connector_id": CONN1, "ocpp_connector_number": 1},
            {"cms_connector_id": CONN2, "ocpp_connector_number": 2},
        ],
    }


class MappingEvolutionTests(unittest.TestCase):
    def assert_blocked(self, before, after, code):
        with self.assertRaises(UnsupportedMappingEvolution) as exc:
            verify_additive_mapping_change(before, after)
        self.assertEqual(exc.exception.code, code)

    def test_exact_snapshot(self):
        a = sample()
        self.assertEqual(verify_additive_mapping_change(a, copy.deepcopy(a)), "UNCHANGED")

    def test_enablement_and_serial_update_are_supported(self):
        a = sample()
        b = copy.deepcopy(a)
        b["enabled"] = False
        b["expected_serial"] = "SERIAL-B"
        self.assertEqual(verify_additive_mapping_change(a, b), "SUPPORTED")

    def test_add_connector_without_rebinding_is_supported(self):
        a = sample()
        b = copy.deepcopy(a)
        b["connectors"].append({"cms_connector_id": OTHER, "ocpp_connector_number": 3})
        self.assertEqual(verify_additive_mapping_change(a, b), "SUPPORTED")

    def test_connector_order_does_not_mean_protocol_reassignment(self):
        a = sample()
        b = copy.deepcopy(a)
        b["connectors"].reverse()
        self.assertEqual(verify_additive_mapping_change(a, b), "SUPPORTED")

    def test_connector_removal_rejected(self):
        a = sample()
        b = copy.deepcopy(a)
        b["connectors"].pop()
        self.assert_blocked(a, b, "HAL_MAPPING_CONNECTOR_CHANGE_UNSUPPORTED")

    def test_connector_renumber_rejected(self):
        a = sample()
        b = copy.deepcopy(a)
        b["connectors"][1]["ocpp_connector_number"] = 3
        self.assert_blocked(a, b, "HAL_MAPPING_CONNECTOR_CHANGE_UNSUPPORTED")

    def test_new_uuid_same_number_rejected(self):
        a = sample()
        b = copy.deepcopy(a)
        b["connectors"][1]["cms_connector_id"] = OTHER
        self.assert_blocked(a, b, "HAL_MAPPING_CONNECTOR_CHANGE_UNSUPPORTED")

    def test_charger_identity_rejected(self):
        a = sample()
        b = copy.deepcopy(a)
        b["charger_ocpp_identity"] = "DIFFERENT-PHYSICAL-CHARGER"
        self.assert_blocked(a, b, "HAL_MAPPING_IDENTITY_CHANGE_UNSUPPORTED")

    def test_cross_scope_rejected(self):
        a = sample()
        b = copy.deepcopy(a)
        b["cpo_id"] = OTHER
        self.assert_blocked(a, b, "HAL_MAPPING_IDENTITY_CHANGE_UNSUPPORTED")

    def test_cms_charger_reassignment_rejected(self):
        a = sample()
        b = copy.deepcopy(a)
        b["cms_charger_id"] = OTHER
        self.assert_blocked(a, b, "HAL_MAPPING_IDENTITY_CHANGE_UNSUPPORTED")

    def test_corrupt_existing_snapshot_fails_closed(self):
        a = sample()
        for mutate in (
            lambda x: x.pop("expected_serial"),
            lambda x: x.update(enabled="true"),
            lambda x: x["connectors"].append(copy.deepcopy(x["connectors"][0])),
            lambda x: x["connectors"][0].update(ocpp_connector_number=True),
        ):
            broken = copy.deepcopy(a)
            mutate(broken)
            self.assert_blocked(broken, a, "HAL_MAPPING_BASELINE_INVALID")

    def test_corrupt_candidate_fails_closed(self):
        a = sample()
        for mutate in (
            lambda x: x.update(cpo_id="invalid"),
            lambda x: x.update(connectors=[]),
            lambda x: x["connectors"][0].update(ocpp_connector_number=0),
            lambda x: x["connectors"][1].update(ocpp_connector_number=1),
        ):
            broken = copy.deepcopy(a)
            mutate(broken)
            self.assert_blocked(a, broken, "HAL_MAPPING_PROPOSAL_INVALID")


if __name__ == "__main__":
    unittest.main()
