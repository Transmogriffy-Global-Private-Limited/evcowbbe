"""Legacy customer API import shim.

New code must import from cow.users.master."""

from cow.users.master import (
    ADDRESS_FIELDS as ADDRESS_FIELDS,
    REQUIRED_BODY as REQUIRED_BODY,
    VEHICLE_FIELDS as VEHICLE_FIELDS,
    address_json as address_json,
    profile_json as profile_json,
    register_customer_routes as register_customer_routes,
    vehicle_json as vehicle_json,
)
