"""Registered Admin Fleet vehicle router, kept separate from the identity API."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from ninja import Router, Schema
from ninja.security import HttpBearer
from pydantic import ConfigDict, Field, field_validator

from cow.errors import reject
from cow.services import auth, idempotency, rbac
from cow.services import fleet as fleet_service


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FleetVehicleCreate(StrictSchema):
    asset_tag: str = Field(min_length=2, max_length=64)
    registration_number: str | None = Field(default=None, min_length=2, max_length=32)
    make: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    variant: str | None = Field(default=None, min_length=1, max_length=120)
    vehicle_type: str | None = Field(default=None, min_length=1, max_length=80)
    ownership_type: str | None = Field(default=None, min_length=1, max_length=80)
    registration_valid_until: date | None = None
    insurance_valid_until: date | None = None
    inspection_status: Literal["UNKNOWN", "VALID", "FAILED", "EXPIRED"] = "UNKNOWN"
    notes: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("asset_tag", "registration_number", mode="after")
    @classmethod
    def normalize_tag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.upper()
        if not re.fullmatch(r"[A-Z0-9_./ -]+", value):
            raise ValueError("invalid fleet tag")
        return value


class FleetVehiclePatch(FleetVehicleCreate):
    asset_tag: str | None = Field(default=None, min_length=2, max_length=64)
    make: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    inspection_status: Literal["UNKNOWN", "VALID", "FAILED", "EXPIRED"] | None = None


class FleetReason(StrictSchema):
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("reason")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("control characters are not permitted")
        return value


class FleetMaintenanceCreate(FleetReason):
    starts_at: datetime
    ends_at: datetime

    @field_validator("ends_at")
    @classmethod
    def after_start(cls, value: datetime, info):
        start = info.data.get("starts_at")
        if start and value <= start:
            raise ValueError("ends_at must be after starts_at")
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include an offset")
        return value


class FleetChargerCreate(StrictSchema):
    asset_tag: str = Field(min_length=2, max_length=64)
    serial_number: str | None = Field(default=None, min_length=2, max_length=120)
    manufacturer: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    rated_power_w: int | None = Field(default=None, ge=1, le=2_147_483_647)
    protocol_family: str | None = Field(default=None, min_length=1, max_length=80)
    protocol_version: str | None = Field(default=None, min_length=1, max_length=80)
    hal_identity: str | None = Field(default=None, min_length=1, max_length=150)
    notes: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("asset_tag", "serial_number", mode="after")
    @classmethod
    def normalize_tag(cls, value: str | None) -> str | None:
        return FleetVehicleCreate.normalize_tag(value)

    @field_validator(
        "manufacturer", "model", "protocol_family", "protocol_version", "hal_identity", "notes"
    )
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("control characters are not permitted")
        return value


class FleetChargerPatch(FleetChargerCreate):
    asset_tag: str | None = Field(default=None, min_length=2, max_length=64)
    manufacturer: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=120)


class FleetConnectorCreate(StrictSchema):
    connector_number: int = Field(ge=1, le=2_147_483_647)
    connector_type: str = Field(min_length=2, max_length=64)
    max_power_w: int = Field(ge=1, le=2_147_483_647)
    supports_ac: bool
    supports_dc: bool
    is_active: bool = True

    @field_validator("connector_type", mode="after")
    @classmethod
    def normalize_connector_type(cls, value: str) -> str:
        normalized = FleetVehicleCreate.normalize_tag(value)
        assert normalized is not None
        return normalized


class FleetConnectorPatch(StrictSchema):
    connector_number: int | None = Field(default=None, ge=1, le=2_147_483_647)
    connector_type: str | None = Field(default=None, min_length=2, max_length=64)
    max_power_w: int | None = Field(default=None, ge=1, le=2_147_483_647)
    supports_ac: bool | None = None
    supports_dc: bool | None = None
    is_active: bool | None = None

    @field_validator("connector_type", mode="after")
    @classmethod
    def normalize_connector_type(cls, value: str | None) -> str | None:
        return FleetVehicleCreate.normalize_tag(value)


class FleetPairCreate(FleetReason):
    vehicle_id: UUID
    charger_id: UUID


class AdminFleetBearer(HttpBearer):
    # The Go contract's bearer credentials are opaque server-side tokens, not JWTs.
    openapi_bearerFormat = "opaque"

    def authenticate(self, request, token):
        return auth.authenticate(token, "admin")


fleet_router = Router(auth=AdminFleetBearer(), tags=["Admin Fleet"])


def _data(request, status: int, payload: dict, *, etag: int | None = None):
    response = JsonResponse({"data": payload}, status=status)
    if etag:
        response["ETag"] = f'"{etag}"'
    return response


def _json_mutation(request) -> None:
    if request.content_type.split(";", 1)[0].lower() != "application/json":
        reject(415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")


def _require(request, capability: str) -> None:
    rbac.require(request.auth, capability)


def _precondition(request) -> int:
    value = request.headers.get("If-Match")
    if value is None:
        reject(428, "PRECONDITION_REQUIRED", "Supply If-Match from a current resource ETag")
    if not re.fullmatch(r'"[1-9][0-9]*"', value.strip()):
        reject(400, "VALIDATION_FAILED", "Invalid If-Match version")
    return int(value.strip()[1:-1])


def _mutation(request, operation: str, payload: dict, callback):
    key = request.headers.get("Idempotency-Key")
    if not key:
        reject(428, "IDEMPOTENCY_KEY_REQUIRED", "Supply one unique Idempotency-Key header")
    if (
        not 16 <= len(key) <= 128
        or any(ord(char) < 33 or ord(char) > 126 for char in key)
        or "," in key
    ):
        reject(422, "VALIDATION_FAILED", "Invalid fleet request")
    try:
        return idempotency.execute(request.auth, operation, key, payload, callback)
    except IntegrityError:
        reject(409, "CONFLICT", "Fleet state conflicts with this operation")


def _vehicle_fields(payload, *, patch: bool) -> dict:
    fields = payload.model_dump(exclude_unset=patch, mode="python")
    if not patch:
        fields = {key: value for key, value in fields.items() if value is not None}
    return fields


def _fields(payload, *, patch: bool) -> dict:
    fields = payload.model_dump(exclude_unset=patch, mode="python")
    if not patch:
        return {key: value for key, value in fields.items() if value is not None}
    return fields


@fleet_router.get("/vehicles", operation_id="adminFleetVehiclesList")
def list_vehicles(
    request,
    limit: int = 50,
    after: UUID | None = None,
    status: str = "",
    active: bool | None = None,
    q: str = "",
):
    _require(request, "fleet.read")
    allowed = {"limit", "after", "status", "active", "q"}
    if (
        any(name not in allowed or len(values) != 1 for name, values in request.GET.lists())
        or not 1 <= limit <= 100
        or len(q.strip()) > 120
        or status not in {"", "AVAILABLE", "OUT_OF_SERVICE", "RETIRED"}
    ):
        reject(422, "VALIDATION_FAILED", "Invalid fleet request")
    vehicles, next_cursor = fleet_service.list_vehicles(
        limit=limit, after=after, status=status, active=active, query=q.strip()
    )
    data = {"items": [fleet_service.vehicle_data(vehicle) for vehicle in vehicles]}
    if next_cursor:
        data["next_cursor"] = next_cursor
    return _data(request, 200, data)


@fleet_router.post("/vehicles", response={201: dict}, operation_id="adminFleetVehiclesCreate")
def create_vehicle(request, payload: FleetVehicleCreate):
    _require(request, "fleet.manage")
    _json_mutation(request)
    fields = _vehicle_fields(payload, patch=False)

    def create():
        with transaction.atomic():
            vehicle = fleet_service.create(request.auth, fields)
            return 201, {"data": fleet_service.vehicle_data(vehicle)}

    status, body = _mutation(
        request, "POST /api/v1/admin/vehicles", payload.model_dump(mode="json"), create
    )
    return _data(request, status, body["data"], etag=body["data"].get("version"))


@fleet_router.get("/vehicles/{vehicle_id}", operation_id="adminFleetVehicleGet")
def get_vehicle(request, vehicle_id: UUID):
    _require(request, "fleet.read")
    vehicle = fleet_service.get(vehicle_id)
    return _data(request, 200, fleet_service.vehicle_data(vehicle), etag=vehicle.version)


@fleet_router.patch("/vehicles/{vehicle_id}", operation_id="adminFleetVehiclePatch")
def patch_vehicle(request, vehicle_id: UUID, payload: FleetVehiclePatch):
    _require(request, "fleet.manage")
    _json_mutation(request)
    version = _precondition(request)
    fields = _vehicle_fields(payload, patch=True)
    if not fields:
        reject(422, "VALIDATION_FAILED", "Invalid fleet request")

    def update():
        with transaction.atomic():
            vehicle = fleet_service.patch(request.auth, vehicle_id, version, fields)
            return 200, {"data": fleet_service.vehicle_data(vehicle)}

    status, body = _mutation(
        request,
        f"PATCH /api/v1/admin/vehicles/{vehicle_id} {version}",
        payload.model_dump(mode="json", exclude_unset=True),
        update,
    )
    return _data(request, status, body["data"], etag=body["data"].get("version"))


@fleet_router.get("/vehicles/{vehicle_id}/history", operation_id="adminFleetVehicleHistory")
def vehicle_history(request, vehicle_id: UUID):
    _require(request, "fleet.read")
    _require(request, "audit.read")
    return _data(request, 200, {"items": fleet_service.history(vehicle_id)})


@fleet_router.get(
    "/vehicles/{vehicle_id}/availability", operation_id="adminFleetVehicleAvailability"
)
def vehicle_availability(request, vehicle_id: UUID):
    _require(request, "fleet.read")
    return _data(request, 200, fleet_service.availability(vehicle_id))


def _lifecycle(request, vehicle_id: UUID, payload: FleetReason, *, active: bool):
    _require(request, "fleet.manage")
    _json_mutation(request)
    version = _precondition(request)

    def change():
        with transaction.atomic():
            vehicle = fleet_service.set_active(
                request.auth, vehicle_id, version, payload.reason, active
            )
            return 200, {"data": fleet_service.vehicle_data(vehicle)}

    action = "reactivate" if active else "deactivate"
    status, body = _mutation(
        request,
        f"POST /api/v1/admin/vehicles/{vehicle_id}/{action} {version}",
        payload.model_dump(mode="json"),
        change,
    )
    return _data(request, status, body["data"], etag=body["data"].get("version"))


@fleet_router.post("/vehicles/{vehicle_id}/deactivate", operation_id="adminFleetVehicleDeactivate")
def deactivate_vehicle(request, vehicle_id: UUID, payload: FleetReason):
    return _lifecycle(request, vehicle_id, payload, active=False)


@fleet_router.post("/vehicles/{vehicle_id}/reactivate", operation_id="adminFleetVehicleReactivate")
def reactivate_vehicle(request, vehicle_id: UUID, payload: FleetReason):
    return _lifecycle(request, vehicle_id, payload, active=True)


@fleet_router.post(
    "/vehicles/{vehicle_id}/maintenance",
    response={201: dict},
    operation_id="adminFleetVehicleMaintenance",
)
def vehicle_maintenance(request, vehicle_id: UUID, payload: FleetMaintenanceCreate):
    _require(request, "fleet.maintenance")
    _json_mutation(request)

    def create():
        with transaction.atomic():
            period = fleet_service.add_maintenance(
                request.auth, vehicle_id, payload.starts_at, payload.ends_at, payload.reason
            )
            return 201, {"data": fleet_service.maintenance_data(period)}

    status, body = _mutation(
        request,
        f"POST /api/v1/admin/vehicles/{vehicle_id}/maintenance",
        payload.model_dump(mode="json"),
        create,
    )
    return _data(request, status, body["data"])


@fleet_router.get("/chargers", operation_id="adminFleetChargersList")
def list_chargers(
    request,
    limit: int = 50,
    after: UUID | None = None,
    status: str = "",
    active: bool | None = None,
    q: str = "",
):
    _require(request, "fleet.read")
    allowed = {"limit", "after", "status", "active", "q"}
    if (
        any(name not in allowed or len(values) != 1 for name, values in request.GET.lists())
        or not 1 <= limit <= 100
        or len(q.strip()) > 120
        or status not in {"", "AVAILABLE", "OUT_OF_SERVICE", "RETIRED"}
    ):
        reject(422, "VALIDATION_FAILED", "Invalid fleet request")
    chargers, next_cursor = fleet_service.list_chargers(
        limit=limit, after=after, status=status, active=active, query=q.strip()
    )
    data = {"items": [fleet_service.charger_data(charger) for charger in chargers]}
    if next_cursor:
        data["next_cursor"] = next_cursor
    return _data(request, 200, data)


@fleet_router.post("/chargers", response={201: dict}, operation_id="adminFleetChargersCreate")
def create_charger(request, payload: FleetChargerCreate):
    _require(request, "fleet.manage")
    _json_mutation(request)
    fields = _fields(payload, patch=False)

    def create():
        charger = fleet_service.create_charger(request.auth, fields)
        return 201, {"data": fleet_service.charger_data(charger)}

    status, body = _mutation(
        request, "POST /api/v1/admin/chargers", payload.model_dump(mode="json"), create
    )
    return _data(request, status, body["data"], etag=body["data"].get("version"))


@fleet_router.get("/chargers/{charger_id}", operation_id="adminFleetChargerGet")
def get_charger(request, charger_id: UUID):
    _require(request, "fleet.read")
    charger = fleet_service.get_charger(charger_id)
    return _data(request, 200, fleet_service.charger_data(charger), etag=charger.version)


@fleet_router.patch("/chargers/{charger_id}", operation_id="adminFleetChargerPatch")
def patch_charger(request, charger_id: UUID, payload: FleetChargerPatch):
    _require(request, "fleet.manage")
    _json_mutation(request)
    version = _precondition(request)
    fields = _fields(payload, patch=True)
    if not fields:
        reject(422, "VALIDATION_FAILED", "Invalid fleet request")

    def update():
        charger = fleet_service.patch_charger(request.auth, charger_id, version, fields)
        return 200, {"data": fleet_service.charger_data(charger)}

    status, body = _mutation(
        request,
        f"PATCH /api/v1/admin/chargers/{charger_id} {version}",
        payload.model_dump(mode="json", exclude_unset=True),
        update,
    )
    return _data(request, status, body["data"], etag=body["data"].get("version"))


@fleet_router.get("/chargers/{charger_id}/history", operation_id="adminFleetChargerHistory")
def charger_history(request, charger_id: UUID):
    _require(request, "fleet.read")
    _require(request, "audit.read")
    return _data(request, 200, {"items": fleet_service.charger_history(charger_id)})


@fleet_router.get(
    "/chargers/{charger_id}/availability", operation_id="adminFleetChargerAvailability"
)
def charger_availability(request, charger_id: UUID):
    _require(request, "fleet.read")
    return _data(request, 200, fleet_service.charger_availability(charger_id))


def _charger_lifecycle(request, charger_id: UUID, payload: FleetReason, *, active: bool):
    _require(request, "fleet.manage")
    _json_mutation(request)
    version = _precondition(request)

    def change():
        charger = fleet_service.set_charger_active(
            request.auth, charger_id, version, payload.reason, active
        )
        return 200, {"data": fleet_service.charger_data(charger)}

    action = "reactivate" if active else "deactivate"
    status, body = _mutation(
        request,
        f"POST /api/v1/admin/chargers/{charger_id}/{action} {version}",
        payload.model_dump(mode="json"),
        change,
    )
    return _data(request, status, body["data"], etag=body["data"].get("version"))


@fleet_router.post("/chargers/{charger_id}/deactivate", operation_id="adminFleetChargerDeactivate")
def deactivate_charger(request, charger_id: UUID, payload: FleetReason):
    return _charger_lifecycle(request, charger_id, payload, active=False)


@fleet_router.post("/chargers/{charger_id}/reactivate", operation_id="adminFleetChargerReactivate")
def reactivate_charger(request, charger_id: UUID, payload: FleetReason):
    return _charger_lifecycle(request, charger_id, payload, active=True)


@fleet_router.post(
    "/chargers/{charger_id}/maintenance",
    response={201: dict},
    operation_id="adminFleetChargerMaintenance",
)
def charger_maintenance(request, charger_id: UUID, payload: FleetMaintenanceCreate):
    _require(request, "fleet.maintenance")
    _json_mutation(request)

    def create():
        period = fleet_service.add_charger_maintenance(
            request.auth, charger_id, payload.starts_at, payload.ends_at, payload.reason
        )
        return 201, {"data": fleet_service.charger_maintenance_data(period)}

    status, body = _mutation(
        request,
        f"POST /api/v1/admin/chargers/{charger_id}/maintenance",
        payload.model_dump(mode="json"),
        create,
    )
    return _data(request, status, body["data"])


@fleet_router.get("/chargers/{charger_id}/connectors", operation_id="adminFleetConnectorsList")
def list_connectors(request, charger_id: UUID):
    _require(request, "fleet.read")
    return _data(
        request,
        200,
        {
            "items": [
                fleet_service.connector_data(item)
                for item in fleet_service.list_connectors(charger_id)
            ]
        },
    )


@fleet_router.post(
    "/chargers/{charger_id}/connectors",
    response={201: dict},
    operation_id="adminFleetConnectorCreate",
)
def create_connector(request, charger_id: UUID, payload: FleetConnectorCreate):
    _require(request, "fleet.manage")
    _json_mutation(request)
    fields = _fields(payload, patch=False)
    fields["active"] = fields.pop("is_active")

    def create():
        connector = fleet_service.create_connector(request.auth, charger_id, fields)
        return 201, {"data": fleet_service.connector_data(connector)}

    status, body = _mutation(
        request,
        f"POST /api/v1/admin/chargers/{charger_id}/connectors",
        payload.model_dump(mode="json"),
        create,
    )
    return _data(request, status, body["data"], etag=body["data"].get("version"))


@fleet_router.patch(
    "/chargers/{charger_id}/connectors/{connector_id}",
    operation_id="adminFleetConnectorPatch",
)
def patch_connector(request, charger_id: UUID, connector_id: UUID, payload: FleetConnectorPatch):
    _require(request, "fleet.manage")
    _json_mutation(request)
    version = _precondition(request)
    fields = _fields(payload, patch=True)
    if not fields:
        reject(422, "VALIDATION_FAILED", "Invalid fleet request")
    if "is_active" in fields:
        fields["active"] = fields.pop("is_active")

    def update():
        connector = fleet_service.patch_connector(
            request.auth, charger_id, connector_id, version, fields
        )
        return 200, {"data": fleet_service.connector_data(connector)}

    status, body = _mutation(
        request,
        f"PATCH /api/v1/admin/chargers/{charger_id}/connectors/{connector_id} {version}",
        payload.model_dump(mode="json", exclude_unset=True),
        update,
    )
    return _data(request, status, body["data"], etag=body["data"].get("version"))


@fleet_router.get("/pairings", operation_id="adminFleetPairingsList")
def list_pairings(
    request,
    limit: int = 50,
    vehicle_id: UUID | None = None,
    charger_id: UUID | None = None,
    status: str = "",
):
    _require(request, "fleet.read")
    allowed = {"limit", "vehicle_id", "charger_id", "status"}
    if (
        any(name not in allowed or len(values) != 1 for name, values in request.GET.lists())
        or not 1 <= limit <= 100
        or status not in {"", "ACTIVE", "ENDED", "INVALIDATED"}
    ):
        reject(422, "VALIDATION_FAILED", "Invalid fleet request")
    pairings = fleet_service.list_pairings(
        vehicle_id=vehicle_id, charger_id=charger_id, status=status, limit=limit
    )
    return _data(request, 200, {"items": [fleet_service.pairing_data(item) for item in pairings]})


@fleet_router.post("/pairings", response={201: dict}, operation_id="adminFleetPairCreate")
def create_pairing(request, payload: FleetPairCreate):
    _require(request, "fleet.manage")
    _json_mutation(request)

    def create():
        pairing = fleet_service.create_pairing(
            request.auth, payload.vehicle_id, payload.charger_id, payload.reason
        )
        return 201, {"data": fleet_service.pairing_data(pairing)}

    status, body = _mutation(
        request, "POST /api/v1/admin/pairings", payload.model_dump(mode="json"), create
    )
    return _data(request, status, body["data"])


@fleet_router.post("/pairings/{pairing_id}/end", operation_id="adminFleetPairEnd")
def end_pairing(request, pairing_id: UUID, payload: FleetReason):
    _require(request, "fleet.manage")
    _json_mutation(request)

    def end():
        pairing = fleet_service.end_pairing(request.auth, pairing_id, payload.reason)
        return 200, {"data": fleet_service.pairing_data(pairing)}

    status, body = _mutation(
        request,
        f"POST /api/v1/admin/pairings/{pairing_id}/end",
        payload.model_dump(mode="json"),
        end,
    )
    return _data(request, status, body["data"])
