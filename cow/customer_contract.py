"""Legacy customer contract import shim.

New code must import from cow.users.contracts."""

from cow.users.contracts import (
    AddressCreate as AddressCreate,
    AddressFields as AddressFields,
    AddressPatch as AddressPatch,
    Coordinate as Coordinate,
    ProfilePatch as ProfilePatch,
    Strict as Strict,
    VehicleCreate as VehicleCreate,
    VehicleFields as VehicleFields,
    VehiclePatch as VehiclePatch,
    merged_address as merged_address,
    merged_vehicle as merged_vehicle,
)
