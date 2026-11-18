"""One authoritative Django ORM schema. Never edit historical migrations."""
import uuid

from django.db import models
from django.db.models import F, Q


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class Account(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=254, unique=True)
    password_hash = models.CharField(max_length=256)
    email_verified = models.BooleanField(default=False)
    two_factor_enabled = models.BooleanField(default=False)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(email__regex=r"^[^A-Z]+$"), name="cow_email_lower")
        ]

class Principal(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="principals")
    plane = models.CharField(max_length=12, choices=[(x,x) for x in ("app","driver","admin")])
    status = models.CharField(
        max_length=12, choices=[(x, x) for x in ("ACTIVE", "PENDING", "SUSPENDED")]
    )
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "plane"], name="cow_principal_per_plane")
        ]

class CustomerProfile(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.OneToOneField(
        Principal, on_delete=models.PROTECT, related_name="customer_profile"
    )
    full_name = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=24, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class AuthSession(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.ForeignKey(Principal, on_delete=models.CASCADE)
    access_digest = models.CharField(max_length=64, unique=True)
    refresh_digest = models.CharField(max_length=64, unique=True)
    access_expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    absolute_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    refresh_generation = models.PositiveIntegerField(default=0)

class AuthRateBucket(models.Model):
    key_digest = models.CharField(max_length=64, primary_key=True)
    hits = models.PositiveIntegerField(default=0)
    window_ends_at = models.DateTimeField()


class ConsumedRefreshToken(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    session = models.ForeignKey(AuthSession, on_delete=models.CASCADE)
    consumed_at = models.DateTimeField(auto_now_add=True)

class AuthChallenge(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    plane = models.CharField(max_length=12)
    purpose = models.CharField(max_length=20)
    code_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_sent_at = models.DateTimeField()

class AuthActionToken(Timestamped):
    digest = models.CharField(max_length=64, primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)

class AdminRole(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.ForeignKey(Principal, on_delete=models.CASCADE)
    role = models.CharField(max_length=32)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["principal", "role"], name="cow_one_role_assignment")
        ]

class AdminOverride(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.ForeignKey(Principal, on_delete=models.CASCADE)
    capability = models.CharField(max_length=100)
    decision = models.CharField(max_length=5, choices=[("ALLOW","ALLOW"),("DENY","DENY")])
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["principal", "capability"], name="cow_one_capability_override"
            )
        ]

class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(Principal, null=True, on_delete=models.SET_NULL)
    target_type = models.CharField(max_length=60)
    target_id = models.UUIDField(null=True)
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)

class Outbox(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=50)
    destination = models.CharField(max_length=120)
    event_key = models.CharField(max_length=160, unique=True)
    payload = models.JSONField(default=dict)
    state = models.CharField(max_length=15, default="PENDING")
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField()
    leased_until = models.DateTimeField(null=True)
    delivered_at = models.DateTimeField(null=True)

class IdempotencyReceipt(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(Principal, on_delete=models.PROTECT)
    scope = models.CharField(max_length=90)
    key = models.CharField(max_length=128)
    request_digest = models.CharField(max_length=64)
    status = models.PositiveSmallIntegerField()
    response = models.JSONField(default=dict)
    expires_at = models.DateTimeField()
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "scope", "key"], name="cow_mutation_once")
        ]

class CustomerVehicle(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(Principal, on_delete=models.PROTECT)
    label = models.CharField(max_length=100, blank=True)
    make = models.CharField(max_length=120)
    model = models.CharField(max_length=120)
    variant = models.CharField(max_length=120, blank=True)
    registration_number = models.CharField(max_length=32, blank=True)
    battery_capacity_wh = models.PositiveBigIntegerField(null=True, blank=True)
    connector_types = models.JSONField(default=list)
    supports_ac = models.BooleanField(default=True)
    supports_dc = models.BooleanField(default=False)
    max_ac_power_w = models.PositiveIntegerField(null=True)
    max_dc_power_w = models.PositiveIntegerField(null=True)
    saved = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

class CustomerAddress(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(Principal, on_delete=models.PROTECT)
    label = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=700)
    line1 = models.CharField(max_length=250, blank=True)
    line2 = models.CharField(max_length=250, blank=True)
    locality = models.CharField(max_length=150, blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    postal_code = models.CharField(max_length=24, blank=True)
    country_code = models.CharField(max_length=2, default="IN")
    landmark = models.CharField(max_length=160, blank=True)
    floor = models.CharField(max_length=60, blank=True)
    contact_instructions = models.CharField(max_length=500, blank=True)
    geocode_provider = models.CharField(max_length=80, blank=True)
    geocode_provider_place_id = models.CharField(max_length=200, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    instructions = models.CharField(max_length=1000, blank=True)
    saved = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner"],
                condition=Q(active=True, is_default=True),
                name="cow_one_active_default_address",
            )
        ]

class ServiceVehicle(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset_tag = models.CharField(max_length=64, unique=True)
    registration_number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    make = models.CharField(max_length=120)
    model = models.CharField(max_length=120)
    variant = models.CharField(max_length=120, null=True, blank=True)
    vehicle_type = models.CharField(max_length=80, null=True, blank=True)
    ownership_type = models.CharField(max_length=80, null=True, blank=True)
    registration_valid_until = models.DateField(null=True, blank=True)
    insurance_valid_until = models.DateField(null=True, blank=True)
    inspection_status = models.CharField(
        max_length=16,
        default="UNKNOWN",
        choices=[(value, value) for value in ("UNKNOWN", "VALID", "FAILED", "EXPIRED")],
    )
    operational_status = models.CharField(max_length=24, default="AVAILABLE")
    maintenance_status = models.CharField(max_length=24, default="CLEAR")
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=4000, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(operational_status="RETIRED") | Q(active=False),
                name="cow_vehicle_retired_inactive",
            )
        ]


class VehicleMaintenancePeriod(models.Model):
    """Append-only planned maintenance; PostgreSQL serializes overlap checks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle = models.ForeignKey(ServiceVehicle, on_delete=models.PROTECT)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=500)
    created_by = models.ForeignKey(Principal, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="cow_vehicle_maintenance_range",
            )
        ]
        indexes = [
            models.Index(
                fields=["vehicle", "starts_at", "ends_at"], name="cow_vehicle_maint_window"
            )
        ]

class Charger(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset_tag = models.CharField(max_length=64, unique=True)
    serial_number = models.CharField(max_length=120, unique=True, null=True, blank=True)
    hal_identity = models.CharField(max_length=150, unique=True, null=True, blank=True)
    manufacturer = models.CharField(max_length=120)
    model = models.CharField(max_length=120)
    rated_power_w = models.PositiveIntegerField(null=True, blank=True)
    protocol_family = models.CharField(max_length=80, null=True, blank=True)
    protocol_version = models.CharField(max_length=80, null=True, blank=True)
    active = models.BooleanField(default=True)
    operational_status = models.CharField(max_length=24, default="AVAILABLE")
    maintenance_status = models.CharField(max_length=24, default="UNKNOWN")
    notes = models.CharField(max_length=4000, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(operational_status="RETIRED") | Q(active=False),
                name="cow_charger_retired_inactive",
            )
        ]

class ChargerConnector(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    connector_number = models.PositiveIntegerField()
    connector_type = models.CharField(max_length=64)
    max_power_w = models.PositiveIntegerField()
    supports_ac = models.BooleanField(default=True)
    supports_dc = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["charger", "connector_number"], name="cow_connector_number_unique"
            ),
            models.CheckConstraint(
                condition=Q(supports_ac=True) | Q(supports_dc=True),
                name="cow_connector_has_current",
            ),
        ]

class VehicleChargerPairing(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle = models.ForeignKey(ServiceVehicle, on_delete=models.PROTECT)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    expected = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16,
        default="ACTIVE",
        choices=[(value, value) for value in ("ACTIVE", "ENDED", "INVALIDATED")],
    )
    source = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        choices=[(value, value) for value in ("ADMIN", "RECONCILIATION")],
    )
    created_by = models.ForeignKey(
        Principal, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_pairings"
    )
    # 0001-0003 did not record pairing creation provenance.  Keep those
    # historical fields NULL rather than manufacturing a migration timestamp,
    # reason, or source.  New service-created rows still receive auto_now_add
    # and explicit source/reason values.
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    reason = models.CharField(max_length=500, null=True, blank=True)
    end_reason = models.CharField(max_length=500, null=True, blank=True)
    ended_by = models.ForeignKey(
        Principal, null=True, blank=True, on_delete=models.SET_NULL, related_name="ended_pairings"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["charger"],
                condition=Q(effective_to__isnull=True),
                name="cow_one_current_vehicle_per_charger",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gt=F("effective_from")),
                name="cow_pairing_time_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="ACTIVE", effective_to__isnull=True)
                    | (~Q(status="ACTIVE") & Q(effective_to__isnull=False))
                ),
                name="cow_pairing_lifecycle",
            ),
        ]


class ChargerMaintenancePeriod(models.Model):
    """Append-only planned charger maintenance; PostgreSQL serializes overlap checks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=500)
    created_by = models.ForeignKey(Principal, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="cow_charger_maintenance_range",
            )
        ]
        indexes = [
            models.Index(
                fields=["charger", "starts_at", "ends_at"], name="cow_charger_maint_window"
            )
        ]

class DriverProfile(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.OneToOneField(Principal, on_delete=models.PROTECT)
    eligibility = models.CharField(max_length=24, default="PENDING")
    licence_valid_until = models.DateField(null=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(eligibility__in=["PENDING", "ACTIVE", "SUSPENDED", "OFFBOARDED"]),
                name="cow_driver_oper_eligibility_valid",
            ),
            models.CheckConstraint(condition=Q(version__gte=1),
                                   name="cow_driver_oper_version_positive"),
        ]

class DutySession(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver = models.ForeignKey(DriverProfile, on_delete=models.PROTECT)
    vehicle = models.ForeignKey(ServiceVehicle, on_delete=models.PROTECT)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    state = models.CharField(max_length=25, default="OFF_DUTY")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)
    observed_pairing = models.JSONField(default=dict)
    schedule = models.ForeignKey("DriverSchedule", on_delete=models.PROTECT, null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["driver"],
                condition=Q(ended_at__isnull=True),
                name="cow_one_active_driver_duty",
            ),
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(ended_at__isnull=True),
                name="cow_one_active_vehicle_claim",
            ),
            models.UniqueConstraint(
                fields=["charger"],
                condition=Q(ended_at__isnull=True),
                name="cow_one_active_charger_claim",
            ),
            models.UniqueConstraint(
                fields=["schedule"], condition=Q(schedule__isnull=False),
                name="cow_c3_one_duty_per_schedule",
            ),
            models.CheckConstraint(condition=Q(version__gte=1),
                                   name="cow_c3_duty_version_positive"),
        ]

class PricingPolicy(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    revision = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default="DRAFT")
    currency = models.CharField(max_length=3, default="INR")
    base_service_paise = models.PositiveBigIntegerField(default=0)
    per_kwh_paise = models.PositiveBigIntegerField(default=0)
    # Additive Admin policy ledger. The legacy ACTIVE quotation path is not
    # automatically enabled by the new PUBLISHED status.
    policy_key = models.CharField(max_length=64, null=True, blank=True)
    policy_version = models.PositiveBigIntegerField(null=True, blank=True)
    scope = models.JSONField(default=dict)
    rules = models.JSONField(default=list)
    state_version = models.PositiveIntegerField(default=1)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        Principal, null=True, blank=True, on_delete=models.PROTECT,
        related_name="authored_pricing_revisions",
    )
    effective_from = models.DateTimeField(null=True)
    effective_to = models.DateTimeField(null=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name","revision"], name="cow_pricing_revision_unique"),
            models.UniqueConstraint(fields=["status"],condition=Q(status="ACTIVE"),name="cow_one_active_pricing_policy"),
            models.UniqueConstraint(
                fields=["policy_key", "policy_version"], condition=Q(policy_key__isnull=False),
                name="cow_admin_pricing_revision_unique",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True)
                | (Q(starts_at__isnull=False) & Q(ends_at__gt=F("starts_at"))),
                name="cow_admin_pricing_window",
            ),
        ]

class ServiceRequest(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Principal, on_delete=models.PROTECT)
    vehicle_snapshot = models.JSONField()
    address_snapshot = models.JSONField()
    requested_energy_wh = models.PositiveBigIntegerField()
    state = models.CharField(max_length=24, default="REQUESTED")

class Quote(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(ServiceRequest, on_delete=models.PROTECT)
    pricing_policy = models.ForeignKey(PricingPolicy, on_delete=models.PROTECT)
    pricing_snapshot = models.JSONField()
    total_paise = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request"],
                condition=Q(accepted_at__isnull=False),
                name="cow_one_accepted_quote_per_request",
            )
        ]

class ServiceJob(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.OneToOneField(ServiceRequest, on_delete=models.PROTECT)
    accepted_quote = models.OneToOneField(Quote, on_delete=models.PROTECT)
    state = models.CharField(max_length=26, default="DISPATCHING")
    driver_duty = models.ForeignKey(DutySession, on_delete=models.PROTECT, null=True)
    version = models.PositiveIntegerField(default=1)
    finalized_at = models.DateTimeField(null=True)

class JobEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(ServiceJob, on_delete=models.PROTECT)
    action = models.CharField(max_length=55)
    from_state = models.CharField(max_length=26)
    to_state = models.CharField(max_length=26)
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class ChargingSession(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(ServiceJob, on_delete=models.PROTECT)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    connector_number = models.PositiveSmallIntegerField()
    hal_transaction_id = models.CharField(max_length=160)
    state = models.CharField(max_length=16, default="ACTIVE")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)
    meter_start_wh = models.BigIntegerField(null=True)
    meter_end_wh = models.BigIntegerField(null=True)
    last_meter_wh = models.BigIntegerField(null=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["charger", "hal_transaction_id"], name="cow_exact_hal_transaction_unique"
            ),
            models.UniqueConstraint(
                fields=["charger", "connector_number"],
                condition=Q(state="ACTIVE"),
                name="cow_one_active_session_per_connector",
            ),
        ]

class HALCommand(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(ServiceJob, on_delete=models.PROTECT)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    params = models.JSONField(default=dict)
    state = models.CharField(max_length=32, default="PENDING")
    attempted_at = models.DateTimeField(null=True)
    outcome_at = models.DateTimeField(null=True)

class HALFact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_id = models.UUIDField(unique=True)
    kind = models.CharField(max_length=45)
    contract_version = models.CharField(max_length=16)
    payload_digest = models.CharField(max_length=64)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    body = models.JSONField()
    processing_state = models.CharField(max_length=16, default="PENDING")
    applied_at = models.DateTimeField(null=True)
    emitted_at = models.DateTimeField()
    ingested_at = models.DateTimeField(auto_now_add=True)

class MeterObservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChargingSession, on_delete=models.PROTECT)
    fact = models.OneToOneField(HALFact, on_delete=models.PROTECT)
    energy_wh = models.BigIntegerField()
    observed_at = models.DateTimeField()

# Sprint 6 additive zone registry models: required for Django migration autodetection.
# One canonical model declaration source. Changes generate migrations via makemigrations.
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
            models.UniqueConstraint(fields=["zone_key"], condition=Q(status="PUBLISHED"), name="cow_zone_single_published_key"),
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

# BEGIN EVCOWBBE COMMERCIAL MACHINE V1 MODELS
# Add to cow/models.py only via the guarded installer. Old models remain untouched.
class CommercialPricingGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=400, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('cow.Principal', on_delete=models.PROTECT, related_name='commercial_groups_created')


class CommercialGroupMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('cow.CustomerProfile', on_delete=models.PROTECT, related_name='commercial_memberships')
    group = models.ForeignKey('cow.CommercialPricingGroup', on_delete=models.PROTECT, related_name='customer_memberships')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey('cow.Principal', on_delete=models.PROTECT, related_name='commercial_memberships_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F('starts_at')), name='cow_cm_group_window')]


class CommercialTariffPayload(models.Model):
    # Existing PricingPolicy is the ONLY publication/version/timing ledger.
    policy = models.OneToOneField('cow.PricingPolicy', primary_key=True, on_delete=models.PROTECT, related_name='commercial_payload')
    channel = models.CharField(max_length=12, choices=[(x,x) for x in ('ROAMING','ON_SITE_QR')])
    plan = models.JSONField()
    created_by = models.ForeignKey('cow.Principal', on_delete=models.PROTECT, related_name='commercial_tariffs_authored')
    created_at = models.DateTimeField(auto_now_add=True)


class CommercialGSTProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile_key = models.CharField(max_length=64)
    profile_version = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=[(x,x) for x in ('DRAFT','APPROVED','RETIRED','REVOKED')], default='DRAFT')
    seller_tax_ref = models.CharField(max_length=200)
    legal_decision_ref = models.CharField(max_length=200)
    tax_classes = models.JSONField()
    created_by = models.ForeignKey('cow.Principal', on_delete=models.PROTECT, related_name='commercial_gst_profiles_created')
    approved_by = models.ForeignKey('cow.Principal', on_delete=models.PROTECT, null=True, blank=True, related_name='commercial_gst_profiles_approved')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['profile_key','profile_version'], name='cow_cm_gst_key_revision'),
            models.CheckConstraint(condition=Q(profile_version__gt=0), name='cow_cm_gst_version_positive'),
        ]


class CommercialTariffAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey('cow.PricingPolicy', on_delete=models.PROTECT, related_name='commercial_target_assignments')
    target_type = models.CharField(max_length=5, choices=[(x,x) for x in ('ZONE','GROUP')])
    zone = models.ForeignKey('cow.ServiceZone', null=True, blank=True, on_delete=models.PROTECT, related_name='commercial_tariff_assignments')
    group = models.ForeignKey('cow.CommercialPricingGroup', null=True, blank=True, on_delete=models.PROTECT, related_name='tariff_assignments')
    channel = models.CharField(max_length=12, choices=[(x,x) for x in ('ROAMING','ON_SITE_QR')])
    slot = models.CharField(max_length=9, choices=[(x,x) for x in ('DEFAULT','SCHEDULED')])
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('cow.Principal', on_delete=models.PROTECT, related_name='commercial_tariff_assignments_created')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(Q(target_type='ZONE',zone__isnull=False,group__isnull=True) | Q(target_type='GROUP',zone__isnull=True,group__isnull=False)), name='cow_cm_tariff_one_target'),
            models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(starts_at__isnull=True) | Q(ends_at__gt=F('starts_at')), name='cow_cm_tariff_assignment_window'),
            models.CheckConstraint(condition=Q(slot='DEFAULT') | Q(slot='SCHEDULED',starts_at__isnull=False,ends_at__isnull=False), name='cow_cm_tariff_schedule_bounded'),
        ]


class CommercialGSTAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    zone = models.ForeignKey('cow.ServiceZone', on_delete=models.PROTECT, related_name='commercial_gst_assignments')
    channel = models.CharField(max_length=12, choices=[(x,x) for x in ('ROAMING','ON_SITE_QR')])
    profile = models.ForeignKey('cow.CommercialGSTProfile', on_delete=models.PROTECT, related_name='zone_assignments')
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('cow.Principal', on_delete=models.PROTECT, related_name='commercial_gst_assignments_created')

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(starts_at__isnull=True) | Q(ends_at__gt=F('starts_at')), name='cow_cm_gst_assignment_window')]


class CommercialZoneChannel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    zone = models.ForeignKey('cow.ServiceZone', on_delete=models.PROTECT, related_name='commercial_channels')
    channel = models.CharField(max_length=12, choices=[(x,x) for x in ('ROAMING','ON_SITE_QR')])
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['zone','channel'], name='cow_cm_zone_channel_unique')]
# END EVCOWBBE COMMERCIAL MACHINE V1 MODELS

# Sprint 9 C1: recruitment evidence is separate from operational DriverProfile.
class DriverRecruitmentPolicy(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_key = models.CharField(max_length=64, default="DEFAULT")
    version = models.PositiveIntegerField()
    required_document_types = models.JSONField(default=list)
    scan_required = models.BooleanField(default=True)
    legal_decision_ref = models.CharField(max_length=200)
    active = models.BooleanField(default=False)
    created_by = models.ForeignKey(Principal, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["policy_key", "version"], name="cow_driver_policy_version_unique"),
            models.UniqueConstraint(fields=["policy_key"], condition=Q(active=True), name="cow_driver_one_active_policy"),
            models.CheckConstraint(condition=Q(version__gte=1), name="cow_driver_policy_positive_version"),
        ]


class DriverApplication(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.OneToOneField(Principal, on_delete=models.PROTECT)
    state = models.CharField(max_length=24, default="DRAFT")
    version = models.PositiveIntegerField(default=1)
    legal_name = models.CharField(max_length=160, default="", blank=True)
    contact_phone = models.CharField(max_length=32, default="", blank=True)
    policy = models.ForeignKey(DriverRecruitmentPolicy, null=True, on_delete=models.PROTECT)
    submitted_at = models.DateTimeField(null=True)
    reviewed_at = models.DateTimeField(null=True)


class DriverDocument(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(DriverApplication, on_delete=models.PROTECT, related_name="documents")
    document_type = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=100, unique=True)
    storage_provider = models.CharField(max_length=24, default="LOCAL")
    content_type = models.CharField(max_length=80)
    original_filename = models.CharField(max_length=255)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    expires_on = models.DateField(null=True)
    replaces = models.ForeignKey("self", on_delete=models.PROTECT, null=True)
    review_status = models.CharField(max_length=20, default="PENDING")
    scan_status = models.CharField(max_length=24, default="NOT_SCANNED")
    uploaded_by = models.ForeignKey(Principal, on_delete=models.PROTECT)

    class Meta:
        indexes = [models.Index(fields=["application", "document_type", "created_at"], name="cow_driver_doc_latest_idx")]


class DriverDocumentReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(DriverDocument, on_delete=models.PROTECT, related_name="reviews")
    actor = models.ForeignKey(Principal, on_delete=models.PROTECT)
    decision = models.CharField(max_length=20)
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class DriverApplicationHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(DriverApplication, on_delete=models.PROTECT, related_name="history")
    actor = models.ForeignKey(Principal, on_delete=models.PROTECT)
    from_state = models.CharField(max_length=24)
    to_state = models.CharField(max_length=24)
    reason = models.CharField(max_length=500)
    event_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    delivery_state = models.CharField(max_length=24, default="NOT_CONFIGURED")
    created_at = models.DateTimeField(auto_now_add=True)


class DriverOperationalDecision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver = models.ForeignKey(
        DriverProfile, on_delete=models.PROTECT, related_name="operational_decisions"
    )
    actor = models.ForeignKey(Principal, on_delete=models.PROTECT)
    action = models.CharField(max_length=16)
    from_status = models.CharField(max_length=24)
    to_status = models.CharField(max_length=24)
    reason = models.CharField(max_length=500)
    result_version = models.PositiveIntegerField()
    application = models.ForeignKey(DriverApplication, on_delete=models.PROTECT, null=True)
    application_version = models.PositiveIntegerField(null=True)
    policy = models.ForeignKey(DriverRecruitmentPolicy, on_delete=models.PROTECT, null=True)
    licence_valid_until = models.DateField(null=True)
    evidence_digest = models.CharField(max_length=64, null=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["driver", "-occurred_at", "-id"],
                                name="cow_driver_oper_history_idx")]
        constraints = [models.UniqueConstraint(
            fields=["driver", "result_version"],
            name="cow_driver_oper_one_decision_version",
        )]

class WalletAccount(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField(
        Principal, on_delete=models.PROTECT, related_name="wallet_account"
    )
    balance_paise = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="INR")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(balance_paise__gte=0),
                name="cow_wallet_balance_nonnegative",
            )
        ]


class WalletTransaction(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(
        WalletAccount, on_delete=models.PROTECT, related_name="transactions"
    )
    transaction_type = models.CharField(max_length=32)
    amount_paise = models.PositiveBigIntegerField()
    balance_after_paise = models.BigIntegerField()
    reference_id = models.CharField(max_length=128, blank=True, default="")
    idempotency_key = models.CharField(max_length=128, blank=True, default="")
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["wallet", "created_at"], name="cow_wallet_tx_created_idx"),
            models.Index(fields=["reference_id"], name="cow_wallet_tx_ref_idx"),
        ]


class PaymentOrder(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(
        Principal, on_delete=models.PROTECT, related_name="payment_orders"
    )
    provider = models.CharField(max_length=32, default="RAZORPAY")
    provider_order_id = models.CharField(max_length=128, unique=True)
    amount_paise = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    state = models.CharField(
        max_length=24,
        choices=[(x, x) for x in ("PENDING", "CAPTURED", "FAILED")],
        default="PENDING",
    )
    provider_payment_id = models.CharField(max_length=128, null=True, blank=True)
    provider_signature = models.CharField(max_length=256, null=True, blank=True)
    receipt = models.CharField(max_length=128, blank=True, default="")
    claim_token = models.CharField(max_length=64, blank=True, default="")
    claim_expires_at = models.DateTimeField(null=True, blank=True)
    error_reason = models.CharField(max_length=500, blank=True, default="")
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "created_at"], name="cow_pay_order_cust_idx"),
            models.Index(fields=["customer", "receipt"], name="cow_pay_order_cust_rcpt_idx"),
            models.Index(fields=["provider_order_id"], name="cow_pay_order_poid_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_payment_id"],
                condition=Q(provider_payment_id__isnull=False),
                name="cow_pay_provider_payment_unique",
            ),
            models.UniqueConstraint(
                fields=["customer", "receipt"],
                condition=~Q(receipt=""),
                name="cow_pay_order_cust_receipt_unique",
            ),
        ]


class CustomerJobRating(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(ServiceJob, on_delete=models.PROTECT, related_name="rating")
    customer = models.ForeignKey(Principal, on_delete=models.PROTECT, related_name="job_ratings")
    rating = models.PositiveSmallIntegerField()
    comment = models.CharField(max_length=500, blank=True, default="")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__gte=1, rating__lte=5),
                name="cow_job_rating_range_1_to_5",
            )
        ]


class CustomerSupportTicket(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Principal, on_delete=models.PROTECT, related_name="support_tickets")
    job = models.ForeignKey(ServiceJob, on_delete=models.PROTECT, null=True, blank=True, related_name="support_tickets")
    subject = models.CharField(max_length=200)
    category = models.CharField(max_length=50, default="GENERAL")
    status = models.CharField(
        max_length=20,
        choices=[(x, x) for x in ("OPEN", "PENDING_SUPPORT", "RESOLVED", "CLOSED")],
        default="OPEN",
    )
    version = models.PositiveIntegerField(default=1)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "created_at"], name="cow_supp_ticket_cust_idx"),
        ]


class CustomerSupportMessage(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(CustomerSupportTicket, on_delete=models.PROTECT, related_name="messages")
    sender = models.ForeignKey(Principal, on_delete=models.PROTECT)
    message = models.TextField(max_length=2000)

    class Meta:
        indexes = [
            models.Index(fields=["ticket", "created_at"], name="cow_supp_msg_tkt_idx"),
        ]


class CustomerNotification(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Principal, on_delete=models.PROTECT, related_name="notifications")
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=500)
    notification_type = models.CharField(max_length=40, default="INFO")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "is_read", "created_at"], name="cow_notif_cust_read_idx"),
        ]


class CustomerNotificationPreference(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField(Principal, on_delete=models.PROTECT, related_name="notification_preferences")
    push_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=True)
    service_updates = models.BooleanField(default=True)
    promotions = models.BooleanField(default=False)


# C3: schedules are planned occurrences; DutySession remains the sole live claim.
class DriverSchedule(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver = models.ForeignKey(DriverProfile, on_delete=models.PROTECT)
    vehicle = models.ForeignKey(ServiceVehicle, on_delete=models.PROTECT)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=16, default="PUBLISHED",
        choices=[(v, v) for v in ("PUBLISHED", "CANCELLED")],
    )
    version = models.PositiveIntegerField(default=1)
    notes = models.CharField(max_length=500, blank=True, default="")
    created_by = models.ForeignKey(Principal, on_delete=models.PROTECT)

    class Meta:
        indexes = [
            models.Index(fields=["driver", "starts_at", "ends_at"], name="cow_c3_schedule_driver_idx"),
            models.Index(fields=["vehicle", "starts_at"], name="cow_c3_schedule_vehicle_idx"),
            models.Index(fields=["charger", "starts_at"], name="cow_c3_schedule_charger_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")),
                                   name="cow_c3_schedule_positive_range"),
            models.CheckConstraint(condition=Q(version__gte=1),
                                   name="cow_c3_schedule_version_positive"),
            models.CheckConstraint(condition=Q(status__in=["PUBLISHED", "CANCELLED"]),
                                   name="cow_c3_schedule_status_valid"),
        ]


class DriverDutyBreak(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    duty = models.ForeignKey(DutySession, on_delete=models.PROTECT)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=500)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(ended_at__isnull=True) |
                                   Q(ended_at__gt=F("started_at")),
                                   name="cow_c3_break_positive_range"),
            models.UniqueConstraint(fields=["duty"], condition=Q(ended_at__isnull=True),
                                    name="cow_c3_one_open_break_per_duty"),
        ]


class DriverDutyEvent(models.Model):
    """Immutable operational history. See 0015 PostgreSQL DB trigger."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    duty = models.ForeignKey(DutySession, on_delete=models.PROTECT)
    actor = models.ForeignKey(Principal, on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    from_state = models.CharField(max_length=25)
    to_state = models.CharField(max_length=25)
    reason = models.CharField(max_length=500)
    result_version = models.PositiveIntegerField()
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["duty", "-occurred_at"], name="cow_c3_duty_history_idx")]
        constraints = [
            models.UniqueConstraint(fields=["duty", "result_version"],
                                    name="cow_c3_one_duty_event_version"),
            models.CheckConstraint(condition=Q(result_version__gte=1),
                                   name="cow_c3_duty_event_version_pos"),
        ]


class DriverAssetMismatch(models.Model):
    """Reported observation, not an authorization to alter fleet truth."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    duty = models.ForeignKey(DutySession, on_delete=models.PROTECT)
    reported_by = models.ForeignKey(Principal, on_delete=models.PROTECT)
    expected_vehicle = models.ForeignKey(ServiceVehicle, on_delete=models.PROTECT)
    expected_charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    observed_vehicle_id = models.UUIDField()
    observed_charger_id = models.UUIDField()
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=16, default="UNRESOLVED",
                              choices=[(v, v) for v in ("UNRESOLVED", "RESOLVED")])
    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["duty"], condition=Q(status="UNRESOLVED"),
                                    name="cow_c3_one_open_mismatch"),
            models.CheckConstraint(condition=Q(status__in=["UNRESOLVED", "RESOLVED"]),
                                   name="cow_c3_mismatch_status_valid"),
        ]
        indexes = [models.Index(fields=["duty", "-reported_at"],
                                name="cow_c3_mismatch_duty_idx")]

# HAL v1 machine integration: independent CoW HAL deployment / registry.
class HALV1Mapping(Timestamped):
    charger = models.OneToOneField('cow.Charger', primary_key=True, on_delete=models.PROTECT)
    scope_id = models.UUIDField()
    ocpp_identity = models.CharField(max_length=150)
    snapshot = models.JSONField(default=dict)
    state = models.CharField(max_length=32, default='PENDING')
    attempted_at = models.DateTimeField(null=True, blank=True)
    synchronized_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, default='', blank=True)
    class Meta:
        indexes = [models.Index(fields=['scope_id', 'state'], name='cow_v1_mapping_scope_idx')]
        constraints = [models.CheckConstraint(condition=Q(state__in=['PENDING','ATTEMPTED','SYNCHRONIZED','RECONCILIATION_REQUIRED']), name='cow_v1_mapping_state_valid')]


class HALV1ChargerOperation(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope_id = models.UUIDField()
    charger = models.ForeignKey('cow.Charger', on_delete=models.PROTECT)
    connector = models.ForeignKey('cow.ChargerConnector', null=True, blank=True, on_delete=models.PROTECT)
    actor = models.ForeignKey('cow.Principal', on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128)
    request_digest = models.CharField(max_length=64)
    correlation_id = models.UUIDField()
    trace_id = models.UUIDField()
    kind = models.CharField(max_length=32)
    snapshot = models.JSONField(default=dict)
    state = models.CharField(max_length=32, default='PERSISTED')
    hal_operation_id = models.UUIDField(null=True, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    outcome_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_ref = models.CharField(max_length=256, default='', blank=True)
    failure_code = models.CharField(max_length=64, default='', blank=True)
    provider_state = models.CharField(max_length=32, default='', blank=True)
    provider_ocpp_result = models.CharField(max_length=80, default='', blank=True)
    result_summary = models.JSONField(default=dict)
    class Meta:
        indexes = [models.Index(fields=['scope_id','state','created_at'], name='cow_v1_op_state_idx')]
        constraints = [
            models.UniqueConstraint(fields=['actor','idempotency_key'], name='cow_v1_op_actor_idem'),
            models.CheckConstraint(condition=Q(state__in=['PERSISTED','DELIVERY_ATTEMPTED','RECEIPT_RECORDED','RECONCILIATION_REQUIRED','REJECTED','CANCELLED_UNSENT','RESOLVED']), name='cow_v1_op_state_valid'),
        ]


class HALV1ChargerRuntime(models.Model):
    charger = models.OneToOneField('cow.Charger', primary_key=True, on_delete=models.PROTECT)
    connection_state = models.CharField(max_length=20, default='UNKNOWN')
    generation = models.BigIntegerField(default=0)
    sequence = models.BigIntegerField(default=0)
    observed_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)


class HALV1ConnectorRuntime(models.Model):
    connector = models.OneToOneField('cow.ChargerConnector', primary_key=True, on_delete=models.PROTECT)
    ocpp_status = models.CharField(max_length=40, default='Unknown')
    sequence = models.BigIntegerField(default=0)
    observed_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)


class HALV1FactReceipt(models.Model):
    fact_id = models.UUIDField(primary_key=True)
    scope_id = models.UUIDField()
    charger = models.ForeignKey('cow.Charger', on_delete=models.PROTECT)
    fact_type = models.CharField(max_length=64)
    digest = models.CharField(max_length=64)
    occurred_at = models.DateTimeField()
    payload = models.JSONField()
    processing_state = models.CharField(max_length=36, default='PENDING_BUSINESS')
    received_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=['scope_id','processing_state','received_at'], name='cow_v1_fact_work_idx')]


class HALV1TraceReceipt(models.Model):
    event_id = models.UUIDField(primary_key=True)
    scope_id = models.UUIDField()
    charger = models.ForeignKey('cow.Charger', on_delete=models.PROTECT)
    digest = models.CharField(max_length=64)
    event_type = models.CharField(max_length=80)
    received_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict)
    class Meta:
        indexes = [models.Index(fields=['charger','-received_at'], name='cow_v1_trace_charger_idx')]


# HAL v1 charging projection. This is separate from legacy HALCommand/ChargingSession.
# A committed commercial permit is required; its issuance is NOT a user API.
class HALV1ChargingPermit(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope_id = models.UUIDField()
    owner = models.ForeignKey('cow.Principal', on_delete=models.PROTECT)
    channel = models.CharField(max_length=12, choices=[(v, v) for v in ('ROAMING', 'FIXED_QR')])
    job = models.ForeignKey('cow.ServiceJob', null=True, blank=True, on_delete=models.PROTECT)
    charger = models.ForeignKey('cow.Charger', on_delete=models.PROTECT)
    connector = models.ForeignKey('cow.ChargerConnector', on_delete=models.PROTECT)
    wallet_hold_reference = models.CharField(max_length=160, unique=True)
    commercial_snapshot = models.JSONField()
    location_evidence = models.JSONField()
    operational_evidence = models.JSONField()
    limit_type = models.CharField(max_length=8)
    energy_limit_wh = models.BigIntegerField(default=0)
    energy_limit_source = models.CharField(max_length=24, default='NONE')
    max_duration_seconds = models.BigIntegerField(default=0)
    duration_limit_source = models.CharField(max_length=24, default='NONE')
    expires_at = models.DateTimeField()
    state = models.CharField(max_length=32, default='RESERVED')
    class Meta:
        indexes = [models.Index(fields=['scope_id','state'], name='cow_v1_permit_scope_idx')]
        constraints = [models.CheckConstraint(condition=Q(state__in=['RESERVED','BOUND','SETTLEMENT_PENDING','RELEASED','SETTLED','RECONCILIATION_REQUIRED']), name='cow_v1_permit_state_valid')]


class HALV1ChargingIntent(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    permit = models.OneToOneField('cow.HALV1ChargingPermit', on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128)
    state = models.CharField(max_length=32, default='PERSISTED')
    credential_ciphertext = models.TextField()
    credential_sha256 = models.CharField(max_length=64)
    credential_expires_at = models.DateTimeField()
    command_expires_at = models.DateTimeField()
    mapping_snapshot = models.JSONField()
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(state__in=['PERSISTED','DELIVERY_ATTEMPTED','ACCEPTED_FOR_DELIVERY','PROTOCOL_ACKNOWLEDGED','ACTUALLY_STARTED','REJECTED','EXPIRED','RECONCILIATION_REQUIRED']), name='cow_v1_intent_state_valid')]


class HALV1ChargingSession(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.OneToOneField('cow.HALV1ChargingIntent', on_delete=models.PROTECT)
    charger = models.ForeignKey('cow.Charger', on_delete=models.PROTECT)
    connector = models.ForeignKey('cow.ChargerConnector', on_delete=models.PROTECT)
    hal_transaction_id = models.UUIDField(unique=True)
    ocpp_transaction_id = models.BigIntegerField()
    state = models.CharField(max_length=32, default='ACTIVE')
    started_at = models.DateTimeField()
    last_meter_wh = models.BigIntegerField()
    meter_start_wh = models.BigIntegerField()
    meter_end_wh = models.BigIntegerField(null=True, blank=True)
    meter_sequence = models.BigIntegerField(default=0)
    meter_observed_at = models.DateTimeField(null=True, blank=True)
    latest_soc_percent = models.CharField(max_length=8, blank=True, default='')
    soc_sequence = models.BigIntegerField(default=0)
    soc_observed_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.CharField(max_length=100, blank=True, default='')
    completion_fact_id = models.UUIDField(null=True, blank=True, unique=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(state__in=['ACTIVE','PHYSICALLY_COMPLETED','RECONCILIATION_REQUIRED','SETTLED']), name='cow_v1_session_state_valid'),
            models.UniqueConstraint(fields=['connector'],condition=Q(state__in=['ACTIVE','RECONCILIATION_REQUIRED']),name='cow_v1_connector_live_guard')]


class HALV1ChargingCommand(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.ForeignKey('cow.HALV1ChargingIntent', on_delete=models.PROTECT)
    session = models.ForeignKey('cow.HALV1ChargingSession', on_delete=models.PROTECT, null=True, blank=True)
    kind = models.CharField(max_length=5, choices=[('START','START'),('STOP','STOP')])
    idempotency_key = models.CharField(max_length=128)
    state = models.CharField(max_length=32, default='PERSISTED')
    snapshot = models.JSONField()
    trace_id = models.UUIDField(default=uuid.uuid4)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    hal_command_id = models.UUIDField(null=True, blank=True)
    provider_state = models.CharField(max_length=32, blank=True, default='')
    attempted_at = models.DateTimeField(null=True, blank=True)
    outcome_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True, default='')
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['intent','kind'], name='cow_v1_intent_cmd_kind_unique'),
            models.CheckConstraint(condition=Q(state__in=['PERSISTED','DELIVERY_ATTEMPTED','ACK_RECORDED','PROTOCOL_ACKNOWLEDGED','MATERIALIZED','REJECTED','EXPIRED','RECONCILIATION_REQUIRED']), name='cow_v1_charge_cmd_state_valid'),
        ]
        indexes = [models.Index(fields=['state','created_at'], name='cow_v1_charge_cmd_queue_idx')]

# CoW booking-commercial V2: a single job guarantee and many physical starts.
# All wallet holds are durable; they do not mutate posted wallet balance.
class CoWMinimumChargingSchedule(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.OneToOneField('cow.PricingPolicy', on_delete=models.PROTECT,
                                  related_name='cow_minimum_charging')
    inside_slabs = models.JSONField()
    outside_slabs = models.JSONField(null=True, blank=True)
    schedule_sha256 = models.CharField(max_length=64)
    authorized_by = models.ForeignKey('cow.Principal', on_delete=models.PROTECT)
    authorization_ref = models.CharField(max_length=200)
    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(schedule_sha256=''),
                                              name='cow_min_schedule_digest_present')]


class CoWBookingCommercialCommitment(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField('cow.ServiceJob', on_delete=models.PROTECT,
                               related_name='commercial_commitment')
    owner = models.ForeignKey('cow.Principal', on_delete=models.PROTECT)
    wallet = models.ForeignKey('cow.WalletAccount', on_delete=models.PROTECT,
                               related_name='cow_booking_commitments')
    booking_key = models.CharField(max_length=128)
    request_sha256 = models.CharField(max_length=64)
    snapshot = models.JSONField()
    snapshot_sha256 = models.CharField(max_length=64)
    fixed_fees_paise = models.PositiveBigIntegerField()
    minimum_charging_paise = models.PositiveBigIntegerField()
    protected_buffer_paise = models.PositiveBigIntegerField()
    reserved_paise = models.PositiveBigIntegerField()
    approved_distance_m = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default='INR')
    state = models.CharField(max_length=28, default='FUNDED')
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['owner','booking_key'],
                                    name='cow_book_key_per_owner'),
            models.CheckConstraint(condition=Q(state__in=[
                'FUNDED','START_PENDING','IN_SERVICE','CLOSING','RECONCILIATION_REQUIRED',
                'SETTLED','CANCELLED']), name='cow_book_commercial_state'),
            models.CheckConstraint(condition=Q(reserved_paise__gte=0),
                                   name='cow_book_reserve_nonnegative'),
        ]
        indexes = [models.Index(fields=['wallet','state'], name='cow_book_wallet_state_idx')]


class CoWBookingChargingAllocation(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey('cow.CoWBookingCommercialCommitment',
        on_delete=models.PROTECT, related_name='allocations')
    permit = models.OneToOneField('cow.HALV1ChargingPermit', on_delete=models.PROTECT,
        related_name='cow_booking_allocation')
    start_key = models.CharField(max_length=128)
    start_sha256 = models.CharField(max_length=64)
    authorized_prior_wh = models.PositiveBigIntegerField()
    authorized_session_wh = models.PositiveBigIntegerField()
    authorized_total_wh = models.PositiveBigIntegerField()
    budget_gross_paise = models.PositiveBigIntegerField()
    proof_sha256 = models.CharField(max_length=64)
    state = models.CharField(max_length=22, default='PREPARED')
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['booking','start_key'], name='cow_book_start_key_once'),
            models.CheckConstraint(condition=Q(authorized_session_wh__gt=0),
                                   name='cow_book_session_positive'),
            models.CheckConstraint(condition=Q(state__in=[
                'PREPARED','STARTED','COMPLETED','CANCELLED_UNSENT','RECONCILIATION_REQUIRED']),
                                   name='cow_book_allocation_state'),
        ]


class CoWBookingFinancialSettlement(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.OneToOneField('cow.CoWBookingCommercialCommitment',
        on_delete=models.PROTECT, related_name='financial_settlement')
    final_gross_paise = models.PositiveBigIntegerField()
    fixed_fees_paise = models.PositiveBigIntegerField()
    charging_gross_paise = models.PositiveBigIntegerField()
    minimum_adjustment_paise = models.PositiveBigIntegerField()
    delivered_wh = models.PositiveBigIntegerField()
    final_snapshot = models.JSONField()
    final_sha256 = models.CharField(max_length=64)
    wallet_transaction = models.OneToOneField('cow.WalletTransaction',
        on_delete=models.PROTECT, null=True, blank=True,
        related_name='cow_booking_settlement')
    state = models.CharField(max_length=30, default='POSTED')
    reason = models.CharField(max_length=100, blank=True, default='')
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(state__in=[
            'POSTED','RECONCILIATION_REQUIRED']), name='cow_book_settlement_state')]


# CoW V3 financial authority and maps-independent operations.
# Historical migrations remain immutable; 0019 owns these tables.
class CoWWalletOperation(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey('cow.WalletAccount', on_delete=models.PROTECT,
                               related_name='cow_wallet_operations')
    operation_key = models.CharField(max_length=128)
    kind = models.CharField(max_length=32)
    amount_paise = models.PositiveBigIntegerField()
    request_sha256 = models.CharField(max_length=64)
    wallet_transaction = models.OneToOneField('cow.WalletTransaction',
        on_delete=models.PROTECT, related_name='cow_authorized_operation')
    state = models.CharField(max_length=16, default='POSTED')
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['wallet','operation_key'],name='cow_wallet_op_key_once'),
            models.CheckConstraint(condition=Q(amount_paise__gt=0),name='cow_wallet_op_positive'),
            models.CheckConstraint(condition=Q(state='POSTED'),name='cow_wallet_op_posted'),
        ]


class CoWLocationObservation(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey('cow.ServiceJob', on_delete=models.PROTECT,
                             related_name='cow_location_observations')
    actor = models.ForeignKey('cow.Principal', on_delete=models.PROTECT)
    client_event_id = models.UUIDField()
    sequence = models.PositiveBigIntegerField()
    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    accuracy_m = models.PositiveIntegerField()
    observed_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    payload_sha256 = models.CharField(max_length=64)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['job','actor','client_event_id'],name='cow_loc_event_unique'),
            models.UniqueConstraint(fields=['job','actor','sequence'],name='cow_loc_sequence_unique'),
            models.CheckConstraint(condition=Q(latitude__gte=-90)&Q(latitude__lte=90),name='cow_loc_lat_valid'),
            models.CheckConstraint(condition=Q(longitude__gte=-180)&Q(longitude__lte=180),name='cow_loc_lon_valid'),
            models.CheckConstraint(condition=Q(accuracy_m__gt=0)&Q(accuracy_m__lte=200),name='cow_loc_accuracy_valid'),
            models.CheckConstraint(condition=Q(sequence__gt=0),name='cow_loc_sequence_positive'),
        ]
        indexes=[models.Index(fields=['job','actor','-sequence'],name='cow_loc_job_actor_seq_idx')]


class CoWBookingPreviewTicket(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey('cow.Principal', on_delete=models.PROTECT)
    request = models.ForeignKey('cow.ServiceRequest', on_delete=models.PROTECT)
    zone = models.ForeignKey('cow.ServiceZone', on_delete=models.PROTECT)
    token_sha256 = models.CharField(max_length=64, unique=True)
    proof_snapshot = models.JSONField()
    summary_sha256 = models.CharField(max_length=64)
    acceptance_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=16,default='ISSUED')
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(state__in=['ISSUED','CONSUMED']),
                                            name='cow_preview_token_state')]


class CoWHandoverTicket(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey('cow.CoWBookingCommercialCommitment',on_delete=models.PROTECT,
                                related_name='cow_handover_tickets')
    owner = models.ForeignKey('cow.Principal',on_delete=models.PROTECT,
                              related_name='cow_handover_owner')
    driver = models.ForeignKey('cow.Principal',on_delete=models.PROTECT,
                               related_name='cow_handover_driver')
    duty = models.ForeignKey('cow.DutySession',on_delete=models.PROTECT)
    issue_key = models.CharField(max_length=128)
    otp_digest = models.CharField(max_length=64)
    connector = models.ForeignKey('cow.ChargerConnector',null=True,blank=True,on_delete=models.PROTECT)
    consent_token_sha256 = models.CharField(max_length=64,blank=True,default='')
    state = models.CharField(max_length=24,default='ISSUED')
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True,blank=True)
    arrival_ref = models.CharField(max_length=160,blank=True,default='')
    consent_ref = models.CharField(max_length=160,blank=True,default='')
    class Meta:
        constraints=[
            models.UniqueConstraint(fields=['booking','issue_key'],name='cow_handover_issue_once'),
            models.CheckConstraint(condition=Q(state__in=['ISSUED','DRIVER_VERIFIED','CONSENT_READY','CONSUMED','EXPIRED']),name='cow_handover_state_valid'),
        ]
        indexes=[models.Index(fields=['booking','state'],name='cow_handover_booking_idx')]


class CoWCompletionReview(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.OneToOneField('cow.CoWBookingCommercialCommitment',on_delete=models.PROTECT,
                                   related_name='cow_completion_review')
    driver = models.ForeignKey('cow.Principal',on_delete=models.PROTECT,
                               related_name='cow_driver_closures')
    driver_ref = models.CharField(max_length=160)
    state = models.CharField(max_length=24,default='DRIVER_PROPOSED')
    reviewed_by = models.ForeignKey('cow.Principal',null=True,blank=True,on_delete=models.PROTECT,
                                    related_name='cow_reviewed_closures')
    review_ref = models.CharField(max_length=160,blank=True,default='')
    review_reason = models.CharField(max_length=500,blank=True,default='')
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(state__in=['DRIVER_PROPOSED','APPROVED','REJECTED']),
                                            name='cow_completion_review_state')]


class CoWWalletRefund(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    settlement = models.ForeignKey('cow.CoWBookingFinancialSettlement',on_delete=models.PROTECT,
                                  related_name='cow_wallet_refunds')
    amount_paise = models.PositiveBigIntegerField()
    request_key = models.CharField(max_length=128)
    reason = models.CharField(max_length=500)
    evidence_ref = models.CharField(max_length=160)
    requested_by = models.ForeignKey('cow.Principal',on_delete=models.PROTECT,
                                     related_name='cow_requested_refunds')
    approved_by = models.ForeignKey('cow.Principal',null=True,blank=True,on_delete=models.PROTECT,
                                    related_name='cow_approved_refunds')
    approval_ref = models.CharField(max_length=160,blank=True,default='')
    state = models.CharField(max_length=16,default='REQUESTED')
    wallet_transaction = models.OneToOneField('cow.WalletTransaction',null=True,blank=True,
                                              on_delete=models.PROTECT)
    class Meta:
        constraints=[
            models.UniqueConstraint(fields=['settlement','request_key'],name='cow_wallet_refund_key'),
            models.CheckConstraint(condition=Q(amount_paise__gt=0),name='cow_wallet_refund_positive'),
            models.CheckConstraint(condition=Q(state__in=['REQUESTED','POSTED','REJECTED']),
                                   name='cow_wallet_refund_state'),
        ]
