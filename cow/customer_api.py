"""Customer-owned master data, guarded writes, and snapshot-safe retirement."""

import re
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.http import JsonResponse
from ninja import Body
from pydantic import ValidationError as PydanticValidationError

from cow.customer_contract import (
    AddressCreate,
    AddressFields,
    AddressPatch,
    ProfilePatch,
    VehicleCreate,
    VehicleFields,
    VehiclePatch,
)
from cow.errors import reject
from cow.models import AuditEvent, CustomerAddress, CustomerProfile, CustomerVehicle, Principal
from cow.services import idempotency

VEHICLE_FIELDS = tuple(VehicleFields.model_fields)
ADDRESS_FIELDS = tuple(AddressFields.model_fields)


def _parse(model, payload):
    try:
        return model.model_validate(payload)
    except PydanticValidationError:
        reject(422, "VALIDATION_FAILED", "Customer master-data fields are invalid")


def _json(request):
    if request.content_type.split(";", 1)[0].lower() != "application/json":
        reject(415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")


def _key(request, *, required):
    key = request.headers.get("Idempotency-Key", "")
    if not key and required:
        reject(428, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required")
    if key and (not 16 <= len(key) <= 128 or not re.fullmatch(r"[!-~]+", key) or "," in key):
        reject(422, "VALIDATION_FAILED", "Invalid Idempotency-Key")
    return key


def _version(request):
    etag = request.headers.get("If-Match", "")
    if not etag:
        reject(428, "PRECONDITION_REQUIRED", "Current If-Match ETag required")
    if not re.fullmatch(r'"(0|[1-9][0-9]*)"', etag.strip()):
        reject(400, "VALIDATION_FAILED", "Invalid If-Match")
    return int(etag.strip()[1:-1])


def _as_response(status, data, *, etag=None):
    response = JsonResponse({"data": data}, status=status)
    if etag is not None:
        response["ETag"] = f'"{etag}"'
    return response


def _replay(actor, scope, key, payload, op):
    if not key:  # Legacy create calls without a key remain compatible.
        status, data = op()
    else:
        status, data = idempotency.execute(actor, scope, key, payload, op)
    result = data["data"]
    return _as_response(status, result, etag=result.get("version"))


def _audit(actor, obj, action, changed=()):
    AuditEvent.objects.create(
        actor=actor,
        target_type=type(obj).__name__,
        target_id=obj.id,
        action=action,
        details={"version": obj.version, "changed_fields": sorted(changed)},
    )


def profile_json(principal):
    profile = CustomerProfile.objects.filter(principal=principal).first()
    return {
        "full_name": profile.full_name if profile else "",
        "phone": profile.phone if profile else None,
        "version": profile.version if profile else 0,
    }


def vehicle_json(v):
    return {
        "id": str(v.id),
        **{name: getattr(v, name) for name in VEHICLE_FIELDS},
        "active": v.active,
        "version": v.version,
        "capability_evidence": "UNVERIFIED_CUSTOMER_DATA",
    }


def address_json(a):
    return {
        "id": str(a.id),
        **{name: format(Decimal(str(getattr(a, name))), ".7f") if name in {"latitude", "longitude"} else getattr(a, name)
           for name in ADDRESS_FIELDS},
        "active": a.active,
        "is_default": a.is_default,
        "version": a.version,
    }


def _vehicle(actor, vehicle_id, *, lock=False):
    query = CustomerVehicle.objects.filter(id=vehicle_id, owner=actor, active=True)
    if lock:
        query = query.select_for_update()
    item = query.first()
    if item is None:
        reject(404, "NOT_FOUND", "Saved EV not found")
    return item


def _address(actor, address_id, *, lock=False):
    query = CustomerAddress.objects.filter(id=address_id, owner=actor, active=True)
    if lock:
        query = query.select_for_update()
    item = query.first()
    if item is None:
        reject(404, "NOT_FOUND", "Address not found")
    return item


def _assert_version(obj, version):
    if obj.version != version:
        reject(412, "RESOURCE_VERSION_MISMATCH", "Resource changed; reload its current ETag")


def _lock_owner(actor):
    # A stable principal lock serializes default assignment even before the
    # first saved address exists. PostgreSQL's partial index remains final guard.
    Principal.objects.select_for_update().get(id=actor.id)


def register_customer_routes(router):
    @router.patch("/profile", operation_id="app_profile_patch")
    def edit_profile(request, payload: dict = Body(...)):
        _json(request)
        patch = _parse(ProfilePatch, payload)
        version = _version(request)
        key = _key(request, required=True)

        def op():
            with transaction.atomic():
                _lock_owner(request.auth)
                obj = CustomerProfile.objects.select_for_update().filter(principal=request.auth).first()
                observed = obj.version if obj else 0
                if observed != version:
                    reject(412, "RESOURCE_VERSION_MISMATCH", "Profile changed; reload")
                if obj is None:
                    obj = CustomerProfile(principal=request.auth)
                for field, value in patch.model_dump(exclude_unset=True).items():
                    setattr(obj, field, value)
                obj.version = observed + 1
                obj.save()
                _audit(request.auth, obj, "PROFILE_UPDATE", patch.model_fields_set)
                return 200, {"data": {
                    "full_name": obj.full_name, "phone": obj.phone, "version": obj.version
                }}

        return _replay(request.auth, "PATCH /app/profile", key,
                       {"etag": version, **patch.model_dump(mode="json", exclude_unset=True)}, op)

    @router.get("/vehicles", operation_id="app_vehicles_list")
    def list_vehicles(request):
        return {"data": [vehicle_json(x) for x in CustomerVehicle.objects.filter(
            owner=request.auth, active=True, saved=True).order_by("created_at", "id")[:100]]}

    @router.post("/vehicles", operation_id="app_vehicles_create")
    def add_vehicle(request, payload: dict = Body(...)):
        _json(request)
        data = _parse(VehicleCreate, payload)
        key = _key(request, required=False)

        def op():
            with transaction.atomic():
                item = CustomerVehicle.objects.create(owner=request.auth, **data.model_dump())
                _audit(request.auth, item, "CREATE", data.model_fields_set)
                return 201, {"data": vehicle_json(item)}

        return _replay(request.auth, "POST /app/vehicles", key,
                       data.model_dump(mode="json"), op)

    @router.get("/vehicles/{vehicle_id}", operation_id="app_vehicles_get")
    def get_vehicle(request, vehicle_id: UUID):
        item = _vehicle(request.auth, vehicle_id)
        return _as_response(200, vehicle_json(item), etag=item.version)

    @router.patch("/vehicles/{vehicle_id}", operation_id="app_vehicles_patch")
    def edit_vehicle(request, vehicle_id: UUID, payload: dict = Body(...)):
        _json(request)
        patch = _parse(VehiclePatch, payload)
        version = _version(request)
        key = _key(request, required=True)

        def op():
            with transaction.atomic():
                obj = _vehicle(request.auth, vehicle_id, lock=True)
                _assert_version(obj, version)
                merged = _parse(VehicleFields, {
                    **{name: getattr(obj, name) for name in VEHICLE_FIELDS},
                    **patch.model_dump(exclude_unset=True),
                })
                # Applies normalized values; no in-place changes to job snapshots.
                for name in VEHICLE_FIELDS:
                    setattr(obj, name, getattr(merged, name))
                obj.version += 1
                obj.save(update_fields=[*VEHICLE_FIELDS, "version", "updated_at"])
                _audit(request.auth, obj, "PATCH", patch.model_fields_set)
                return 200, {"data": vehicle_json(obj)}

        return _replay(request.auth, f"PATCH /app/vehicles/{vehicle_id}", key,
                       {"etag": version, **patch.model_dump(mode="json", exclude_unset=True)}, op)

    @router.delete("/vehicles/{vehicle_id}", operation_id="app_vehicles_delete")
    def retire_vehicle(request, vehicle_id: UUID):
        version = _version(request)
        key = _key(request, required=True)

        def op():
            with transaction.atomic():
                obj = _vehicle(request.auth, vehicle_id, lock=True)
                _assert_version(obj, version)
                obj.active = False
                obj.saved = False
                obj.version += 1
                obj.save(update_fields=["active", "saved", "version", "updated_at"])
                _audit(request.auth, obj, "DEACTIVATE")
                return 200, {"data": vehicle_json(obj)}

        return _replay(request.auth, f"DELETE /app/vehicles/{vehicle_id}", key,
                       {"etag": version, "vehicle_id": str(vehicle_id)}, op)

    @router.get("/addresses", operation_id="app_addresses_list")
    def list_addresses(request):
        return {"data": [address_json(x) for x in CustomerAddress.objects.filter(
            owner=request.auth, active=True, saved=True).order_by("created_at", "id")[:100]]}

    @router.post("/addresses", operation_id="app_addresses_create")
    def add_address(request, payload: dict = Body(...)):
        _json(request)
        data = _parse(AddressCreate, payload)
        key = _key(request, required=False)

        def op():
            with transaction.atomic():
                _lock_owner(request.auth)
                default_exists = CustomerAddress.objects.filter(
                    owner=request.auth, active=True, is_default=True).exists()
                item = CustomerAddress.objects.create(
                    owner=request.auth, **data.model_dump(),
                    is_default=bool(data.saved and not default_exists),
                )
                _audit(request.auth, item, "CREATE", data.model_fields_set)
                return 201, {"data": address_json(item)}

        return _replay(request.auth, "POST /app/addresses", key,
                       data.model_dump(mode="json"), op)

    @router.get("/addresses/{address_id}", operation_id="app_addresses_get")
    def get_address(request, address_id: UUID):
        item = _address(request.auth, address_id)
        return _as_response(200, address_json(item), etag=item.version)

    @router.patch("/addresses/{address_id}", operation_id="app_addresses_patch")
    def edit_address(request, address_id: UUID, payload: dict = Body(...)):
        _json(request)
        patch = _parse(AddressPatch, payload)
        version = _version(request)
        key = _key(request, required=True)

        def op():
            with transaction.atomic():
                _lock_owner(request.auth)
                obj = _address(request.auth, address_id, lock=True)
                _assert_version(obj, version)
                merged = _parse(AddressFields, {
                    **{name: getattr(obj, name) for name in ADDRESS_FIELDS},
                    **patch.model_dump(exclude_unset=True),
                })
                if obj.is_default and not merged.saved:
                    reject(409, "DEFAULT_ADDRESS_CONFLICT", "Cannot unsave the default address")
                for name in ADDRESS_FIELDS:
                    setattr(obj, name, getattr(merged, name))
                obj.version += 1
                obj.save(update_fields=[*ADDRESS_FIELDS, "version", "updated_at"])
                _audit(request.auth, obj, "PATCH", patch.model_fields_set)
                return 200, {"data": address_json(obj)}

        return _replay(request.auth, f"PATCH /app/addresses/{address_id}", key,
                       {"etag": version, **patch.model_dump(mode="json", exclude_unset=True)}, op)

    @router.delete("/addresses/{address_id}", operation_id="app_addresses_delete")
    def retire_address(request, address_id: UUID):
        version = _version(request)
        key = _key(request, required=True)

        def op():
            with transaction.atomic():
                _lock_owner(request.auth)
                obj = _address(request.auth, address_id, lock=True)
                _assert_version(obj, version)
                obj.active = False
                obj.saved = False
                obj.is_default = False
                obj.version += 1
                obj.save(update_fields=["active", "saved", "is_default", "version", "updated_at"])
                _audit(request.auth, obj, "DEACTIVATE")
                return 200, {"data": address_json(obj)}

        return _replay(request.auth, f"DELETE /app/addresses/{address_id}", key,
                       {"etag": version, "address_id": str(address_id)}, op)

    @router.post("/addresses/{address_id}/make-default", operation_id="app_addresses_default")
    def make_default(request, address_id: UUID):
        version = _version(request)
        key = _key(request, required=True)

        def op():
            with transaction.atomic():
                _lock_owner(request.auth)
                item = _address(request.auth, address_id, lock=True)
                _assert_version(item, version)
                if not item.saved:
                    reject(409, "ADDRESS_NOT_SAVED", "Temporary address cannot be default")
                if not item.is_default:
                    # A stable principal row lock serializes this switch.
                    previous = CustomerAddress.objects.select_for_update().filter(
                        owner=request.auth, active=True, is_default=True
                    ).exclude(id=item.id).first()
                    if previous is not None:
                        previous.is_default = False
                        previous.version += 1
                        previous.save(update_fields=["is_default", "version", "updated_at"])
                        _audit(request.auth, previous, "UNSET_DEFAULT")
                    item.is_default = True
                    item.version += 1
                    item.save(update_fields=["is_default", "version", "updated_at"])
                    _audit(request.auth, item, "MAKE_DEFAULT")
                return 200, {"data": address_json(item)}

        return _replay(request.auth, f"POST /app/addresses/{address_id}/make-default", key,
                       {"etag": version, "address_id": str(address_id)}, op)
