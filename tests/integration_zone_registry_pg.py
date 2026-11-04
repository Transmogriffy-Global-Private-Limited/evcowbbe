"""Run ONLY against an explicitly approved disposable PostgreSQL test DB.

These are not launched by install, nor by generic unittest discover.
On the disposable DB, after reviewed 0008 migration, invoke:
python -m pytest tests/integration_zone_registry_pg.py -q
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import IntegrityError

from cow.domain.zone_tariff_convergence import TrustedCommercialContext
from cow.models import Account, Principal, ServiceZone, ZoneGSTAssignment
from cow.services.zone_commercial_registry import approved_zone_gst

pytestmark = pytest.mark.django_db(transaction=True)
AT = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


def principal(plane="admin"):
    account = Account.objects.create(email=f"zone-{uuid4().hex}@example.invalid",password_hash="not-a-login")
    return Principal.objects.create(account=account, plane=plane, status="ACTIVE")


def zone(admin, key=None):
    record=ServiceZone.objects.create(
        zone_key=key or f"Z{uuid4().hex[:10]}", version=1, name="Test zone",
        center_latitude=Decimal("22.5000000"), center_longitude=Decimal("88.3000000"),
        radius_m=5000, restriction_mode="STRICT", status="DRAFT", created_by=admin,
    )
    record.status="PUBLISHED"
    record.save(update_fields=["status"])
    return record


def gst_record(admin, record, channel="ROAMING", start=AT, end=None):
    obj=ZoneGSTAssignment.objects.create(
        zone=record, channel=channel, binding_version=1, starts_at=start, ends_at=end,
        seller_tax_ref="FIXTURE_SELLER", legal_decision_ref="FIXTURE_APPROVED_TAX_CLASS",
        tax_profiles={"CHARGING": {"approved": True,"sgst_percent": "0","cgst_percent": "0","igst_percent": "0"}},
        authorized_by=admin, authorization_ref="APPROVAL:TEST", status="DRAFT",
    )
    obj.status="APPROVED"
    obj.save(update_fields=["status"])
    return obj


def context(key, channel="ROAMING", instant=AT):
    return TrustedCommercialContext(zone_id=key,channel=channel,relation="INSIDE",
        decision_at=instant,provenance="VERIFIED_TEST",driver_duty_id=str(uuid4()) if channel=="ROAMING" else None,
        site_id=str(uuid4()) if channel=="ON_SITE_QR" else None)


def test_zone_published_immutable():
    admin=principal(); item=zone(admin)
    item.radius_m=5100
    with pytest.raises(IntegrityError):
        item.save(update_fields=["radius_m"])


def test_non_admin_zone_creator_rejected():
    driver=principal("driver")
    with pytest.raises(IntegrityError):
        ServiceZone.objects.create(zone_key=f"Z{uuid4().hex[:10]}",version=1,
            name="invalid",center_latitude=Decimal("0"),center_longitude=Decimal("0"),
            radius_m=100,restriction_mode="STRICT",status="DRAFT",created_by=driver)


def test_gst_effective_and_channel_separated():
    admin=principal(); item=zone(admin)
    gst_record(admin,item)
    found=approved_zone_gst(item,context(item.zone_key))
    assert found.zone_id==item.zone_key and found.channel=="ROAMING"
    from cow.domain.zone_tariff_convergence import TaxBindingUnavailable
    with pytest.raises(TaxBindingUnavailable):
        approved_zone_gst(item,context(item.zone_key,channel="ON_SITE_QR"))


def test_overlapping_approved_gst_rejected():
    admin=principal(); item=zone(admin)
    gst_record(admin,item,start=AT,end=AT+timedelta(days=2))
    next_item=ZoneGSTAssignment.objects.create(
        zone=item, channel="ROAMING", binding_version=2, starts_at=AT+timedelta(days=1),
        ends_at=AT+timedelta(days=3), seller_tax_ref="FIXTURE_SELLER",
        legal_decision_ref="FIXTURE_APPROVED_TAX_CLASS",
        tax_profiles={"CHARGING": {"approved":True,"sgst_percent":"0","cgst_percent":"0","igst_percent":"0"}},
        authorized_by=admin,authorization_ref="APPROVAL:TEST",status="DRAFT")
    next_item.status="APPROVED"
    with pytest.raises(IntegrityError):
        next_item.save(update_fields=["status"])


def test_adjacent_gst_windows_allowed():
    admin=principal(); item=zone(admin)
    gst_record(admin,item,start=AT,end=AT+timedelta(days=1))
    next_item=ZoneGSTAssignment.objects.create(
        zone=item,channel="ROAMING",binding_version=2,starts_at=AT+timedelta(days=1),
        ends_at=AT+timedelta(days=2),seller_tax_ref="FIXTURE_SELLER",
        legal_decision_ref="FIXTURE_APPROVED_TAX_CLASS",
        tax_profiles={"CHARGING":{"approved":True,"sgst_percent":"0","cgst_percent":"0","igst_percent":"0"}},
        authorized_by=admin,authorization_ref="APPROVAL:TEST",status="DRAFT")
    next_item.status="APPROVED"; next_item.save(update_fields=["status"])
    found=approved_zone_gst(item,context(item.zone_key,instant=AT+timedelta(days=1)))
    assert found.binding_version==2
