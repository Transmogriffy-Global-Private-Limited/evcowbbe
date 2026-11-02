"""Pinned Go response subset proof and concrete Django DTO regression fixtures.

This is declaration-level verification only. It never connects to a database.
"""
from __future__ import annotations

import unittest
from uuid import uuid4

from cow.api_parity_responses import (
    AvailabilityEnvelope, HistoryPageEnvelope, PricingListEnvelope,
    VehicleEnvelope, VehiclePageEnvelope,
)
from scripts.check_go_wire_contract import _success_response_refines


def _canonical(schema, defs):
    if isinstance(schema, list):
        return [_canonical(x, defs) for x in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        return _canonical(defs[schema["$ref"].rsplit("/", 1)[1]], defs)
    allowed = {"type", "properties", "required", "additionalProperties", "items",
               "format", "enum", "const", "minimum", "maximum", "minLength",
               "maxLength", "minItems", "maxItems", "anyOf", "pattern"}
    result = {}
    for key,value in schema.items():
        if key not in allowed:
            continue
        if key == "properties":
            result[key] = {name: _canonical(child, defs) for name,child in value.items()}
        else:
            result[key] = _canonical(value, defs)
    return result


def _model_schema(cls):
    schema = cls.model_json_schema()
    return _canonical(schema, schema.get("$defs", {}))


def _response(schema, *, etag=False):
    headers = {"X-Request-ID": {"type": "string", "format": "uuid"}}
    if etag:
        headers["ETag"] = {"type": "string"}
    return {"content": {"application/json": schema}, "headers": headers}


class GoResponseRefinementTests(unittest.TestCase):
    def test_asset_id_and_version_contract(self):
        asset = {"type": "object", "properties": {
            "id": {"type": "string", "format": "uuid"},
            "version": {"type": "integer", "minimum": 1},
        }, "required": ["id"], "additionalProperties": True}
        go = {"type": "object", "properties": {"data": {"anyOf": [asset]}},
              "required": ["data"]}
        self.assertTrue(_success_response_refines(
            _response(go, etag=True), _response(_model_schema(VehicleEnvelope), etag=True)))
        self.assertFalse(_success_response_refines(
            _response(go, etag=True), _response(_model_schema(VehicleEnvelope))))

    def test_list_contract(self):
        item = {"type": "object", "properties": {
            "id": {"type": "string", "format": "uuid"}},
            "required": ["id"], "additionalProperties": True}
        go = {"type": "object", "properties": {"data": {
            "type": "object", "properties": {"items": {"type": "array", "items": {
                "anyOf": [item]}}, "next_cursor": {"type": "string", "format": "uuid"}},
            "required": ["items"]}}, "required": ["data"]}
        self.assertTrue(_success_response_refines(
            _response(go), _response(_model_schema(VehiclePageEnvelope))))

    def test_pricing_list_limit_is_not_suppressed(self):
        go = {"type": "object", "properties": {"data": {
            "type": "array", "maxItems": 100, "items": {"type": "object"}}},
            "required": ["data"]}
        py = _model_schema(PricingListEnvelope)
        self.assertTrue(_success_response_refines(_response(go), _response(py)))
        py["properties"]["data"].pop("maxItems")
        self.assertFalse(_success_response_refines(_response(go), _response(py)))

    def test_availability_required_fields(self):
        props = {"active_pairings": {"type": "integer"}, "asset_id": {"type": "string"},
                 "dispatch_eligibility": {"type": "string"},
                 "hal_connectivity": {"type": "string"},
                 "inventory_state": {"type": "string"},
                 "maintenance_now": {"type": "boolean"}}
        go = {"type": "object", "properties": {"data": {
            "type": "object", "properties": props, "required": sorted(props)}},
            "required": ["data"]}
        self.assertTrue(_success_response_refines(
            _response(go), _response(_model_schema(AvailabilityEnvelope))))

    def test_history_source_outputs(self):
        event = {"id": str(uuid4()), "asset_id": str(uuid4()), "asset_kind": "CHARGER",
                 "action": "UPDATE", "created_at": "2026-11-02T05:00:00+00:00"}
        HistoryPageEnvelope.model_validate({"data": {"items": [event]}})
        bad = dict(event, asset_id=None)
        with self.assertRaises(Exception):
            HistoryPageEnvelope.model_validate({"data": {"items": [bad]}})


if __name__ == "__main__":
    unittest.main()
