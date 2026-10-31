"""Go nested pricing topology must agree across Django service and PostgreSQL trigger."""
from datetime import timedelta
from uuid import uuid4
import pytest
from django.utils import timezone
from django.db import transaction, IntegrityError
from cow.errors import DomainError
from cow.models import Account, Principal, AdminRole, PricingPolicy
from cow.pricing_contract import Draft
from cow.services import pricing_admin

pytestmark=[pytest.mark.postgres,pytest.mark.django_db(transaction=True)]

def actor():
    a=Account.objects.create(email=f"pricing-{uuid4().hex}@example.test", password_hash="x",email_verified=True)
    p=Principal.objects.create(account=a,plane="admin",status="ACTIVE")
    AdminRole.objects.create(principal=p,role="FULL_ADMIN")
    return p

def make(actor,version,start,end):
    payload={"policy_key":"NESTED","policy_version":version,"scope":{"kind":"SERVICE_ZONE","key":"ZONE_1"},
        "currency":"INR","rules":[{"id":"VISIT","kind":"BASE","component":"VISIT","basis":"VISITS","rate":"1.25","tax_class":"REVIEW_PENDING"}],
        "reason":"Reviewed pricing topology", "starts_at":start, "ends_at":end}
    d=Draft.model_validate(payload)
    p=pricing_admin.create(actor,d)
    return p

def test_nested_publication_allowed_crossing_rejected():
    p=actor(); now=timezone.now()
    outer=make(p,1,now-timedelta(days=2),now+timedelta(days=8))
    nested=make(p,2,now,now+timedelta(days=2))
    crossing=make(p,3,now+timedelta(days=1),now+timedelta(days=10))
    pricing_admin.publish(p,outer.id,1,"Reviewed broader window")
    pricing_admin.publish(p,nested.id,1,"Reviewed strictly nested window")
    with pytest.raises(DomainError):
        pricing_admin.publish(p,crossing.id,1,"Reject crossing price window")
    crossing.refresh_from_db()
    assert crossing.status=="DRAFT"
    # Direct SQL/ORM bypass must not be able to publish an invalid crossing.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PricingPolicy.objects.filter(pk=crossing.pk).update(status="PUBLISHED", state_version=2)
    assert PricingPolicy.objects.filter(status="PUBLISHED",policy_key="NESTED").count()==2
    terms=pricing_admin.resolve_trusted([{"kind":"SERVICE_ZONE","key":"ZONE_1"}],now+timedelta(hours=1))
    assert terms["policy_version"]==2


def test_pricing_write_rechecks_capability_inside_transaction():
    manager = actor()
    AdminRole.objects.filter(principal=manager).delete()
    draft = Draft.model_validate({
        "policy_key": "ROLE_REVOKED", "policy_version": 1,
        "scope": {"kind": "GLOBAL", "key": "DEFAULT"},
        "currency": "INR",
        "rules": [{"id": "VISIT", "kind": "BASE", "component": "VISIT",
                   "basis": "VISITS", "rate": "1", "tax_class": "UNAPPROVED"}],
        "reason": "Valid but unauthorized pricing change",
    })
    with pytest.raises(DomainError):
        pricing_admin.create(manager, draft)
    assert not PricingPolicy.objects.filter(policy_key="ROLE_REVOKED").exists()
