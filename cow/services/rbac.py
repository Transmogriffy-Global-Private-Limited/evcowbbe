"""Normative Annex 44 admin capabilities: exact grants; explicit DENY wins."""

from cow.errors import reject
from cow.models import AdminOverride, AdminRole

CAPABILITIES = (
    "users.read",
    "users.manage",
    "drivers.read",
    "drivers.review",
    "drivers.manage",
    "drivers.suspend",
    "fleet.read",
    "fleet.manage",
    "fleet.maintenance",
    "fleet.assign",
    "schedules.read",
    "schedules.manage",
    "dispatch.read",
    "dispatch.manage",
    "dispatch.override",
    "jobs.read",
    "jobs.manage",
    "jobs.cancel",
    "jobs.reassign",
    "charging.read",
    "charging.command",
    "charging.reconcile",
    "charging.trace.read",
    "pricing.read",
    "pricing.manage",
    "payments.read",
    "payments.manage",
    "refunds.manage",
    "invoices.read",
    "invoices.manage",
    "compensation.read",
    "compensation.manage",
    "compensation.adjust",
    "ratings.read",
    "support.read",
    "support.reply",
    "support.manage",
    "incidents.read",
    "incidents.manage",
    "notifications.read",
    "notifications.manage",
    "exports.manage",
    "system.settings.read",
    "system.settings.manage",
    "audit.read",
)

ROLE_DEFAULTS = {
    "FULL_ADMIN": frozenset(CAPABILITIES),
    "OPERATIONS": frozenset(
        {
            "users.read",
            "drivers.read",
            "drivers.manage",
            "fleet.read",
            "fleet.manage",
            "fleet.maintenance",
            "fleet.assign",
            "schedules.read",
            "schedules.manage",
            "dispatch.read",
            "dispatch.manage",
            "dispatch.override",
            "jobs.read",
            "jobs.manage",
            "jobs.cancel",
            "jobs.reassign",
            "charging.read",
            "charging.command",
            "charging.reconcile",
            "ratings.read",
            "support.read",
            "support.reply",
            "support.manage",
            "incidents.read",
            "incidents.manage",
            "notifications.read",
            "exports.manage",
            "system.settings.read",
            "audit.read",
        }
    ),
    "DRIVER_RECRUITMENT": frozenset(
        {
            "drivers.read",
            "drivers.review",
            "drivers.manage",
            "drivers.suspend",
            "schedules.read",
            "fleet.read",
            "incidents.read",
            "support.read",
            "audit.read",
        }
    ),
    "FINANCE_COMMERCIAL": frozenset(
        {
            "users.read",
            "jobs.read",
            "pricing.read",
            "pricing.manage",
            "payments.read",
            "payments.manage",
            "refunds.manage",
            "invoices.read",
            "invoices.manage",
            "compensation.read",
            "compensation.manage",
            "compensation.adjust",
            "ratings.read",
            "support.read",
            "incidents.read",
            "exports.manage",
            "audit.read",
        }
    ),
    "SUPPORT": frozenset(
        {
            "users.read",
            "drivers.read",
            "jobs.read",
            "charging.read",
            "payments.read",
            "invoices.read",
            "ratings.read",
            "support.read",
            "support.reply",
            "support.manage",
            "incidents.read",
            "incidents.manage",
            "notifications.read",
        }
    ),
    "TECHNICAL_HAL": frozenset(
        {
            "fleet.read",
            "charging.read",
            "charging.command",
            "charging.reconcile",
            "charging.trace.read",
            "jobs.read",
            "incidents.read",
            "incidents.manage",
            "system.settings.read",
            "audit.read",
        }
    ),
    "READ_ONLY": frozenset(
        {
            "users.read",
            "drivers.read",
            "fleet.read",
            "schedules.read",
            "dispatch.read",
            "jobs.read",
            "charging.read",
            "pricing.read",
            "payments.read",
            "invoices.read",
            "compensation.read",
            "ratings.read",
            "support.read",
            "incidents.read",
            "notifications.read",
            "system.settings.read",
        }
    ),
}


def permitted(roles: set[str], overrides: dict[str, str], capability: str) -> bool:
    if capability not in CAPABILITIES:
        return False
    if overrides.get(capability) == "DENY":
        return False
    if overrides.get(capability) == "ALLOW":
        return True
    return any(capability in ROLE_DEFAULTS.get(role, ()) for role in roles)


def snapshot(principal):
    roles = set(AdminRole.objects.filter(principal=principal).values_list("role", flat=True))
    overrides = dict(
        AdminOverride.objects.filter(principal=principal).values_list("capability", "decision")
    )
    return {
        "roles": sorted(roles),
        "overrides": overrides,
        "effective_capabilities": sorted(c for c in CAPABILITIES if permitted(roles, overrides, c)),
    }


def require(principal, capability):
    if principal.plane != "admin":
        reject(403, "PRINCIPAL_PLANE_MISMATCH", "Access not permitted for this principal")
    state = snapshot(principal)
    if capability not in state["effective_capabilities"]:
        reject(403, "CAPABILITY_DENIED", "Administrative capability required")
