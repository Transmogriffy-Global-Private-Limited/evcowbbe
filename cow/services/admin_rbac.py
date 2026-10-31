"""PostgreSQL-serialized, transaction-authorized Admin RBAC mutations.

The caller's API authorization is not the final check. Each mutation checks
fresh actor eligibility/capabilities *after* acquiring the global RBAC lock,
then proves a verified active manager still exists before commit.
"""
import unicodedata
from django.db import connection, transaction
from cow.errors import reject
from cow.models import AdminOverride, AdminRole, AuditEvent, Principal
from cow.services import rbac

RBAC_LOCK = 37281642373182361


def _lock_global():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [RBAC_LOCK])


def _reason(reason):
    if not isinstance(reason, str):
        reject(422, "VALIDATION_FAILED", "Invalid RBAC reason")
    clean = reason.strip()
    if not 8 <= len(clean) <= 500 or any(unicodedata.category(c).startswith("C") for c in clean):
        reject(422, "VALIDATION_FAILED", "Invalid RBAC reason")
    return clean


def _require_locked_actor(actor, capability="system.settings.manage"):
    # Always reread principal, status and email eligibility from PostgreSQL.
    current = Principal.objects.select_for_update().select_related("account").filter(
        pk=actor.pk, plane="admin", status="ACTIVE", account__email_verified=True
    ).first()
    if current is None:
        reject(403, "ACCOUNT_NOT_READY", "Administrative account not eligible")
    rbac.require(current, capability)
    return current


def _target(actor, account_id):
    if str(actor.account_id) == str(account_id):
        reject(409, "RBAC_SELF_CHANGE_FORBIDDEN", "Cannot modify own administrative grants")
    target = Principal.objects.select_for_update().filter(
        account_id=account_id, plane="admin", status="ACTIVE"
    ).first()
    if target is None:
        reject(404, "NOT_FOUND", "Active Admin account not found")
    return target


def _last_full_admin(target):
    return not AdminRole.objects.filter(
        role="FULL_ADMIN", principal__plane="admin", principal__status="ACTIVE",
        principal__account__email_verified=True,
    ).exclude(principal=target).exists()


def _manager_survives():
    # Stronger than last-role check: explicit DENY can make a Full Admin
    # incapable of repairing the permission registry.
    eligible = Principal.objects.filter(
        plane="admin", status="ACTIVE", account__email_verified=True
    ).order_by("id")
    if not any("system.settings.manage" in rbac.snapshot(p)["effective_capabilities"] for p in eligible):
        reject(409, "LAST_RBAC_MANAGER", "Change would strand all eligible Admin managers")


def _audit(actor, target, action, reason, **changes):
    AuditEvent.objects.create(actor=actor, target_type="AdminRBAC", target_id=target.id,
                              action=action, details={"reason": reason, **changes})


def grant_role(actor, account_id, role, reason):
    reason = _reason(reason)
    if role not in rbac.ROLE_DEFAULTS:
        reject(400, "ROLE_UNKNOWN", "Unknown administrative role")
    with transaction.atomic():
        _lock_global()
        actor = _require_locked_actor(actor)
        target = _target(actor, account_id)
        assigned, created = AdminRole.objects.get_or_create(principal=target, role=role)
        if created:
            _audit(actor, target, "ASSIGN_ROLE", reason, role=role, assignment_id=str(assigned.id))
        _manager_survives()
    return {"ok": True}


def revoke_role(actor, account_id, role, reason):
    reason = _reason(reason)
    if role not in rbac.ROLE_DEFAULTS:
        reject(400, "ROLE_UNKNOWN", "Unknown administrative role")
    with transaction.atomic():
        _lock_global()
        actor = _require_locked_actor(actor)
        target = _target(actor, account_id)
        assignment = AdminRole.objects.select_for_update().filter(principal=target, role=role).first()
        if assignment is None:
            reject(409, "RBAC_CONFLICT", "Role assignment not present")
        if role == "FULL_ADMIN" and _last_full_admin(target):
            reject(409, "LAST_FULL_ADMIN", "Cannot remove the final active verified Full Admin")
        assignment.delete()
        _manager_survives()
        _audit(actor, target, "REMOVE_ROLE", reason, role=role)
    return {"ok": True}


def put_override(actor, account_id, capability, decision, reason):
    reason = _reason(reason)
    if capability not in rbac.CAPABILITIES or decision not in ("ALLOW", "DENY"):
        reject(400, "CAPABILITY_UNKNOWN", "Invalid administrative capability/decision")
    with transaction.atomic():
        _lock_global()
        actor = _require_locked_actor(actor)
        target = _target(actor, account_id)
        if (capability == "system.settings.manage" and decision == "DENY" and
            AdminRole.objects.filter(principal=target, role="FULL_ADMIN").exists() and
            _last_full_admin(target)):
            reject(409, "LAST_FULL_ADMIN", "Cannot disable the final Full Admin")
        obj = AdminOverride.objects.select_for_update().filter(principal=target, capability=capability).first()
        if obj is None:
            AdminOverride.objects.create(principal=target, capability=capability, decision=decision)
            changed = True
        elif obj.decision != decision:
            obj.decision = decision
            obj.save(update_fields=["decision", "updated_at"])
            changed = True
        else:
            changed = False
        _manager_survives()
        if changed:
            _audit(actor, target, "SET_OVERRIDE", reason, capability=capability, decision=decision)
    return {"ok": True}


def clear_override(actor, account_id, capability, reason):
    reason = _reason(reason)
    if capability not in rbac.CAPABILITIES:
        reject(400, "CAPABILITY_UNKNOWN", "Unknown administrative capability")
    with transaction.atomic():
        _lock_global()
        actor = _require_locked_actor(actor)
        target = _target(actor, account_id)
        obj = AdminOverride.objects.select_for_update().filter(principal=target, capability=capability).first()
        if obj is not None:
            obj.delete()
        _manager_survives()
        if obj is not None:
            _audit(actor, target, "REMOVE_OVERRIDE", reason, capability=capability)
    return {"ok": True}
