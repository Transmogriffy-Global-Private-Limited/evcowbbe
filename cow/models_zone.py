"""Django persistence for trusted zone commercial context (registered by cow.models).

Immutable commercial identity is tied to a versioned zone, not a GPS guess.
These models do not activate billing, mutate wallets, or issue HAL commands.
"""
import uuid
from django.db import models
from django.db.models import F, Q


class ServiceZone(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    zone_key = models.CharField(max_length=160)
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=160)
    center_latitude = models.DecimalField(max_digits=10, decimal_places=7)
    center_longitude = models.DecimalField(max_digits=10, decimal_places=7)
    radius_m = models.PositiveIntegerField()
    restriction_mode = models.CharField(max_length=8, choices=[("STRICT", "STRICT"), ("ADVISORY", "ADVISORY")])
    status = models.CharField(max_length=12, choices=[("DRAFT", "DRAFT"), ("PUBLISHED", "PUBLISHED"), ("RETIRED", "RETIRED")], default="DRAFT")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("cow.Principal", on_delete=models.PROTECT, related_name="authored_service_zones")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["zone_key", "version"], name="cow_zone_key_revision_unique"),
            models.CheckConstraint(condition=Q(version__gt=0), name="cow_zone_version_positive"),
            models.CheckConstraint(condition=Q(radius_m__gt=0), name="cow_zone_radius_positive"),
            models.CheckConstraint(condition=Q(center_latitude__gte=-90) & Q(center_latitude__lte=90), name="cow_zone_lat_range"),
            models.CheckConstraint(condition=Q(center_longitude__gte=-180) & Q(center_longitude__lte=180), name="cow_zone_lon_range"),
            models.CheckConstraint(condition=Q(ends_at__isnull=True) | (Q(starts_at__isnull=False) & Q(ends_at__gt=F("starts_at"))), name="cow_zone_valid_window"),
        ]


class DutyZoneAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    duty = models.ForeignKey("cow.DutySession", on_delete=models.PROTECT, related_name="zone_assignments")
    zone = models.ForeignKey("cow.ServiceZone", on_delete=models.PROTECT, related_name="duty_assignments")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    authorized_by = models.ForeignKey("cow.Principal", on_delete=models.PROTECT, related_name="authorized_duty_zones")
    authorization_ref = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["duty", "starts_at"], name="cow_duty_zone_start_unique"),
            models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="cow_duty_zone_valid_window"),
        ]


class ChargerZoneAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    charger = models.ForeignKey("cow.Charger", on_delete=models.PROTECT, related_name="zone_assignments")
    zone = models.ForeignKey("cow.ServiceZone", on_delete=models.PROTECT, related_name="charger_assignments")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    authorized_by = models.ForeignKey("cow.Principal", on_delete=models.PROTECT, related_name="authorized_charger_zones")
    authorization_ref = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["charger", "starts_at"], name="cow_charger_zone_start_unique"),
            models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="cow_charger_zone_valid_window"),
        ]


class ZoneGSTAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    zone = models.ForeignKey("cow.ServiceZone", on_delete=models.PROTECT, related_name="gst_assignments")
    channel = models.CharField(max_length=12, choices=[("ROAMING", "ROAMING"), ("ON_SITE_QR", "ON_SITE_QR")])
    binding_version = models.PositiveIntegerField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    seller_tax_ref = models.CharField(max_length=200)
    legal_decision_ref = models.CharField(max_length=200)
    tax_profiles = models.JSONField()
    status = models.CharField(max_length=10, choices=[("DRAFT", "DRAFT"), ("APPROVED", "APPROVED")], default="DRAFT")
    authorized_by = models.ForeignKey("cow.Principal", on_delete=models.PROTECT, related_name="approved_zone_gst")
    authorization_ref = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["zone", "channel", "binding_version"], name="cow_zone_gst_version_unique"),
            models.CheckConstraint(condition=Q(binding_version__gt=0), name="cow_zone_gst_ver_positive"),
            models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="cow_zone_gst_valid_window"),
        ]


class ZoneTariffPayload(models.Model):
    """One versioned payload attached to one existing PricingPolicy ledger row.

    This is not a second tariff publication ledger. Publication status/time
    are owned exclusively by the referenced PricingPolicy.
    """
    policy = models.OneToOneField("cow.PricingPolicy", primary_key=True, on_delete=models.PROTECT, related_name="zone_payload")
    zone = models.ForeignKey("cow.ServiceZone", on_delete=models.PROTECT, related_name="tariff_payloads")
    channel = models.CharField(max_length=12, choices=[("ROAMING", "ROAMING"), ("ON_SITE_QR", "ON_SITE_QR")])
    plan = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    authorized_by = models.ForeignKey("cow.Principal", on_delete=models.PROTECT, related_name="authored_zone_tariff_payloads")
