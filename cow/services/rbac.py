"""Permission checking cannot be bypassed by presenting an admin token alone."""

from cow.errors import reject
from cow.models import AdminOverride, AdminRole

CAPABILITIES = (
    "system.settings.read",
    "system.settings.manage",
    "fleet.read",
    "fleet.manage",
    "fleet.maintenance",
    "audit.read",
    "pricing.read",
    "pricing.manage",
    "customer.read",
    "driver.read",
    "driver.manage",
    "dispatch.read",
    "dispatch.manage",
    "charging.read",
    "charging.manage",
    "finance.read",
    "finance.manage",
    "support.read",
    "support.manage",
    "technical_hal.read",
    "technical_hal.manage",
)
ROLE_DEFAULTS = {
    "FULL_ADMIN": frozenset(CAPABILITIES),
    "OPERATIONS": frozenset(
        {
            "fleet.read",
            "fleet.manage",
            "fleet.maintenance",
            "audit.read",
            "dispatch.read",
            "dispatch.manage",
            "charging.read",
            "driver.read",
        }
    ),
    "DRIVER_RECRUITMENT": frozenset({"driver.read", "driver.manage"}),
    "FINANCE_COMMERCIAL": frozenset(
        {"finance.read", "finance.manage", "pricing.read", "pricing.manage"}
    ),
    "SUPPORT": frozenset({"support.read", "support.manage", "customer.read"}),
    "TECHNICAL_HAL": frozenset({"technical_hal.read", "technical_hal.manage", "charging.read"}),
    "READ_ONLY": frozenset({c for c in CAPABILITIES if c.endswith(".read")}),
}


def permitted(roles: set[str], overrides: dict[str, str], capability: str) -> bool:
    if capability not in CAPABILITIES:
        return False
    if overrides.get(capability) == "DENY":
        return False
    if overrides.get(capability) == "ALLOW":
        return True
    return any(capability in ROLE_DEFAULTS.get(role, ()) for role in roles)


def require(principal, capability):
    if principal.plane != "admin":
        reject(403, "PRINCIPAL_PLANE_MISMATCH", "Access not permitted for this principal")
    roles = set(AdminRole.objects.filter(principal=principal).values_list("role", flat=True))
    overrides = dict(
        AdminOverride.objects.filter(principal=principal).values_list("capability", "decision")
    )
    if not permitted(roles, overrides, capability):
        reject(403, "CAPABILITY_DENIED", "Administrative capability required")
