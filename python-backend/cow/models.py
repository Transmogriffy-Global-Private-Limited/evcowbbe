"""One authoritative Django ORM schema. Never edit historical migrations."""
import uuid
from django.db import models
from django.db.models import Q, F


def uid():
    return uuid.uuid4()

class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class Account(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    email = models.EmailField(max_length=254, unique=True)
    password_hash = models.CharField(max_length=256)
    email_verified = models.BooleanField(default=False)
    two_factor_enabled = models.BooleanField(default=False)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(email__regex=r'^[^A-Z]+$'), name="cow_email_lower")]

class Principal(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="principals")
    plane = models.CharField(max_length=12, choices=[(x,x) for x in ("app","driver","admin")])
    status = models.CharField(max_length=12, choices=[(x,x) for x in ("ACTIVE","PENDING","SUSPENDED")])
    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "plane"], name="cow_principal_per_plane")]

class AuthSession(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
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
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
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
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    principal = models.ForeignKey(Principal, on_delete=models.CASCADE)
    role = models.CharField(max_length=32)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["principal","role"], name="cow_one_role_assignment")]

class AdminOverride(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    principal = models.ForeignKey(Principal, on_delete=models.CASCADE)
    capability = models.CharField(max_length=100)
    decision = models.CharField(max_length=5, choices=[("ALLOW","ALLOW"),("DENY","DENY")])
    class Meta:
        constraints = [models.UniqueConstraint(fields=["principal","capability"], name="cow_one_capability_override")]

class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    actor = models.ForeignKey(Principal, null=True, on_delete=models.SET_NULL)
    target_type = models.CharField(max_length=60)
    target_id = models.UUIDField(null=True)
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)

class Outbox(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
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
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    actor = models.ForeignKey(Principal, on_delete=models.PROTECT)
    scope = models.CharField(max_length=90)
    key = models.CharField(max_length=128)
    request_digest = models.CharField(max_length=64)
    status = models.PositiveSmallIntegerField()
    response = models.JSONField(default=dict)
    expires_at = models.DateTimeField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=["actor", "scope", "key"], name="cow_mutation_once")]

class CustomerVehicle(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    owner = models.ForeignKey(Principal, on_delete=models.PROTECT)
    label = models.CharField(max_length=100, blank=True)
    make = models.CharField(max_length=120)
    model = models.CharField(max_length=120)
    registration_number = models.CharField(max_length=32, blank=True)
    connector_types = models.JSONField(default=list)
    supports_ac = models.BooleanField(default=True)
    supports_dc = models.BooleanField(default=False)
    max_ac_power_w = models.PositiveIntegerField(null=True)
    max_dc_power_w = models.PositiveIntegerField(null=True)
    saved = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

class CustomerAddress(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    owner = models.ForeignKey(Principal, on_delete=models.PROTECT)
    label = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=700)
    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    instructions = models.CharField(max_length=1000, blank=True)
    saved = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner"], condition=Q(active=True,is_default=True), name="cow_one_active_default_address")]

class ServiceVehicle(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    asset_tag = models.CharField(max_length=64, unique=True)
    registration_number = models.CharField(max_length=32, blank=True)
    make = models.CharField(max_length=120)
    model = models.CharField(max_length=120)
    operational_status = models.CharField(max_length=24, default="AVAILABLE")
    maintenance_status = models.CharField(max_length=24, default="CLEAR")
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

class Charger(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    asset_tag = models.CharField(max_length=64, unique=True)
    hal_identity = models.CharField(max_length=128, unique=True)
    manufacturer = models.CharField(max_length=120)
    model = models.CharField(max_length=120)
    rated_power_w = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    operational_status = models.CharField(max_length=24, default="AVAILABLE")
    version = models.PositiveIntegerField(default=1)

class ChargerConnector(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    connector_number = models.PositiveSmallIntegerField()
    connector_type = models.CharField(max_length=40)
    max_power_w = models.PositiveIntegerField()
    supports_dc = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["charger", "connector_number"], name="cow_connector_number_unique")]

class VehicleChargerPairing(models.Model):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    vehicle = models.ForeignKey(ServiceVehicle, on_delete=models.PROTECT)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True)
    expected = models.BooleanField(default=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["charger"], condition=Q(effective_to__isnull=True), name="cow_one_current_vehicle_per_charger"),
        ]

class DriverProfile(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    principal = models.OneToOneField(Principal, on_delete=models.PROTECT)
    eligibility = models.CharField(max_length=24, default="PENDING")
    licence_valid_until = models.DateField(null=True)

class DutySession(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    driver = models.ForeignKey(DriverProfile, on_delete=models.PROTECT)
    vehicle = models.ForeignKey(ServiceVehicle, on_delete=models.PROTECT)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    state = models.CharField(max_length=25, default="OFF_DUTY")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)
    observed_pairing = models.JSONField(default=dict)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["driver"], condition=Q(ended_at__isnull=True), name="cow_one_active_driver_duty"),
            models.UniqueConstraint(fields=["vehicle"], condition=Q(ended_at__isnull=True), name="cow_one_active_vehicle_claim"),
            models.UniqueConstraint(fields=["charger"], condition=Q(ended_at__isnull=True), name="cow_one_active_charger_claim"),
        ]

class PricingPolicy(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    name = models.CharField(max_length=120)
    revision = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default="DRAFT")
    currency = models.CharField(max_length=3, default="INR")
    base_service_paise = models.PositiveBigIntegerField(default=0)
    per_kwh_paise = models.PositiveBigIntegerField(default=0)
    effective_from = models.DateTimeField(null=True)
    effective_to = models.DateTimeField(null=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name","revision"], name="cow_pricing_revision_unique"),
            models.UniqueConstraint(fields=["status"],condition=Q(status="ACTIVE"),name="cow_one_active_pricing_policy"),
        ]

class ServiceRequest(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    customer = models.ForeignKey(Principal, on_delete=models.PROTECT)
    vehicle_snapshot = models.JSONField()
    address_snapshot = models.JSONField()
    requested_energy_wh = models.PositiveBigIntegerField()
    state = models.CharField(max_length=24, default="REQUESTED")

class Quote(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    request = models.ForeignKey(ServiceRequest, on_delete=models.PROTECT)
    pricing_policy = models.ForeignKey(PricingPolicy, on_delete=models.PROTECT)
    pricing_snapshot = models.JSONField()
    total_paise = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["request"], condition=Q(accepted_at__isnull=False), name="cow_one_accepted_quote_per_request")]

class ServiceJob(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    request = models.OneToOneField(ServiceRequest, on_delete=models.PROTECT)
    accepted_quote = models.OneToOneField(Quote, on_delete=models.PROTECT)
    state = models.CharField(max_length=26, default="DISPATCHING")
    driver_duty = models.ForeignKey(DutySession, on_delete=models.PROTECT, null=True)
    version = models.PositiveIntegerField(default=1)
    finalized_at = models.DateTimeField(null=True)

class JobEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    job = models.ForeignKey(ServiceJob, on_delete=models.PROTECT)
    action = models.CharField(max_length=55)
    from_state = models.CharField(max_length=26)
    to_state = models.CharField(max_length=26)
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class ChargingSession(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
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
            models.UniqueConstraint(fields=["charger", "hal_transaction_id"], name="cow_exact_hal_transaction_unique"),
            models.UniqueConstraint(fields=["charger", "connector_number"], condition=Q(state="ACTIVE"), name="cow_one_active_session_per_connector"),
        ]

class HALCommand(Timestamped):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    job = models.ForeignKey(ServiceJob, on_delete=models.PROTECT)
    charger = models.ForeignKey(Charger, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    params = models.JSONField(default=dict)
    state = models.CharField(max_length=32, default="PENDING")
    attempted_at = models.DateTimeField(null=True)
    outcome_at = models.DateTimeField(null=True)

class HALFact(models.Model):
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
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
    id = models.UUIDField(primary_key=True, default=uid, editable=False)
    session = models.ForeignKey(ChargingSession, on_delete=models.PROTECT)
    fact = models.OneToOneField(HALFact, on_delete=models.PROTECT)
    energy_wh = models.BigIntegerField()
    observed_at = models.DateTimeField()
