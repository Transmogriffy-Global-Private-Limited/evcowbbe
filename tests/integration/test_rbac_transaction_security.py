"""Verifies the service itself refuses stale/unauthorized access after auth boundary."""
from uuid import uuid4
import pytest
from django.test import Client
from cow.errors import DomainError
from cow.models import Account, Principal, AdminRole, AdminOverride
from cow.services import admin_rbac

pytestmark=[pytest.mark.postgres,pytest.mark.django_db(transaction=True)]

def principal(role=None, verified=True):
    a=Account.objects.create(email=f"rbac-{uuid4().hex}@example.test",password_hash="x",email_verified=verified)
    p=Principal.objects.create(account=a,plane="admin",status="ACTIVE")
    if role:AdminRole.objects.create(principal=p,role=role)
    return p

def test_stale_actor_authority_is_rechecked_in_transaction():
    actor=principal("FULL_ADMIN")
    survivor=principal("FULL_ADMIN")
    target=principal("READ_ONLY")
    # Caller retains stale actor Python instance even after its privileges disappear.
    AdminRole.objects.filter(principal=actor).delete()
    with pytest.raises(DomainError):admin_rbac.grant_role(actor,target.account_id,"OPERATIONS","Explicit role grant")
    assert not AdminRole.objects.filter(principal=target,role="OPERATIONS").exists()
    AdminRole.objects.create(principal=actor,role="FULL_ADMIN")
    actor.account.email_verified=False
    actor.account.save(update_fields=["email_verified"])
    with pytest.raises(DomainError):admin_rbac.put_override(actor,target.account_id,"fleet.manage","ALLOW","Unauthorized override attempt")
    assert not AdminOverride.objects.filter(principal=target,capability="fleet.manage").exists()

def test_last_manager_survives_override_and_unverified_admin_excluded():
    actor=principal()
    AdminOverride.objects.create(principal=actor, capability="system.settings.manage", decision="ALLOW")
    target=principal("FULL_ADMIN",verified=False)
    other=principal("FULL_ADMIN")
    # The unverified target cannot count as the final operational administrator.
    with pytest.raises(DomainError):admin_rbac.revoke_role(actor,other.account_id,"FULL_ADMIN","Guard the final active admin")
    assert AdminRole.objects.filter(principal=other,role="FULL_ADMIN").exists()


def test_service_rejects_control_character_in_reason():
    actor=principal("FULL_ADMIN")
    target=principal("READ_ONLY")
    with pytest.raises(DomainError):admin_rbac.grant_role(actor,target.account_id,"SUPPORT","Unsafe\nreason")
