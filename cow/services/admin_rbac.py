"""Atomic admin-to-admin role/override changes with last-admin protection."""

from django.db import connection, transaction

from cow.errors import reject
from cow.models import AdminOverride, AdminRole, AuditEvent, Principal
from cow.services import rbac

# Serialize all authorization grants to prevent concurrent last-admin revocations.
RBAC_LOCK = 37281642373182361


def _target(actor, account_id):
    if str(actor.account_id) == str(account_id):
        reject(409, "RBAC_SELF_CHANGE_FORBIDDEN", "Cannot modify own administrative grants")
    target = (
        Principal.objects.select_for_update().filter(account_id=account_id, plane="admin").first()
    )
    if target is None:
        reject(404, "NOT_FOUND", "Admin account not found")
    return target


def _lock_global():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [RBAC_LOCK])


def _last_full_admin(target):
    return (
        AdminRole.objects.filter(role="FULL_ADMIN", principal__status="ACTIVE")
        .exclude(principal=target)
        .count()
        == 0
    )


def _audit(actor, target, action, reason, **changes):
    AuditEvent.objects.create(
        actor=actor,
        target_type="AdminRBAC",
        target_id=target.id,
        action=action,
        details={"reason": reason, **changes},
    )


def grant_role(actor, account_id, role, reason):
    if role not in rbac.ROLE_DEFAULTS:
        reject(400, "ROLE_UNKNOWN", "Unknown administrative role")
    with transaction.atomic():
        _lock_global()
        target = _target(actor, account_id)
        assigned, created = AdminRole.objects.get_or_create(principal=target, role=role)
        if created:
            _audit(actor, target, "ASSIGN_ROLE", reason, role=role, assignment_id=str(assigned.id))
    return {"ok": True}


def revoke_role(actor, account_id, role, reason):
    if role not in rbac.ROLE_DEFAULTS:
        reject(400, "ROLE_UNKNOWN", "Unknown administrative role")
    with transaction.atomic():
        _lock_global()
        target = _target(actor, account_id)
        assignment = (
            AdminRole.objects.select_for_update().filter(principal=target, role=role).first()
        )
        if assignment is None:
            reject(409, "RBAC_CONFLICT", "Role assignment not present")
        if role == "FULL_ADMIN" and _last_full_admin(target):
            reject(409, "LAST_FULL_ADMIN", "Cannot remove the final active Full Admin")
        assignment.delete()
        _audit(actor, target, "REMOVE_ROLE", reason, role=role)
    return {"ok": True}


def put_override(actor, account_id, capability, decision, reason):
    if capability not in rbac.CAPABILITIES:
        reject(400, "CAPABILITY_UNKNOWN", "Unknown administrative capability")
    with transaction.atomic():
        _lock_global()
        target = _target(actor, account_id)
        if (
            capability == "system.settings.manage"
            and decision == "DENY"
            and AdminRole.objects.filter(principal=target, role="FULL_ADMIN").exists()
            and _last_full_admin(target)
        ):
            reject(409, "LAST_FULL_ADMIN", "Cannot disable the final Full Admin")
        obj = (
            AdminOverride.objects.select_for_update()
            .filter(principal=target, capability=capability)
            .first()
        )
        if obj is None:
            AdminOverride.objects.create(principal=target, capability=capability, decision=decision)
            changed = True
        elif obj.decision != decision:
            obj.decision = decision
            obj.save(update_fields=["decision", "updated_at"])
            changed = True
        else:
            changed = False
        if changed:
            _audit(actor, target, "SET_OVERRIDE", reason, capability=capability, decision=decision)
    return {"ok": True}


def clear_override(actor, account_id, capability, reason):
    if capability not in rbac.CAPABILITIES:
        reject(400, "CAPABILITY_UNKNOWN", "Unknown administrative capability")
    with transaction.atomic():
        _lock_global()
        target = _target(actor, account_id)
        obj = (
            AdminOverride.objects.select_for_update()
            .filter(principal=target, capability=capability)
            .first()
        )
        if obj is not None:
            obj.delete()
            _audit(actor, target, "REMOVE_OVERRIDE", reason, capability=capability)
    return {"ok": True}
