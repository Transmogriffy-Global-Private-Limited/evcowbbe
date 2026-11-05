"""Historical Admin controls import shim.

New code must import from cow.admin.controls."""

from cow.admin.controls import (
    OverrideChange as OverrideChange,
    Reason as Reason,
    RoleChange as RoleChange,
    StrictSchema as StrictSchema,
    register as register,
)
