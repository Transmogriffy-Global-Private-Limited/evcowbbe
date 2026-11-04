"""Additive, reviewed zone/zone-GST/assignment/payload ledger (PostgreSQL only).

REQUIRES independent backup, disposable PG acceptance, and migration approval.
Never apply this to live dev/production merely by installing source files.
"""
import uuid
import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F, Q

FORWARD = r"""
-- Authorized actor checks are performed in each trigger; API/service RBAC
-- revalidation is additionally required before allowing any write endpoint.
CREATE FUNCTION cow_zone_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Versioned Zone rows cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF (SELECT plane FROM cow_principal WHERE id = NEW.created_by_id) IS DISTINCT FROM 'admin' THEN
    RAISE EXCEPTION 'Zone creator must be Admin principal' USING ERRCODE = '23514';
  END IF;
  IF NEW.zone_key !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$' THEN
    RAISE EXCEPTION 'Invalid zone key' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status <> 'DRAFT' THEN
    RAISE EXCEPTION 'Zone starts in DRAFT' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF (OLD.zone_key, OLD.version, OLD.name, OLD.center_latitude, OLD.center_longitude,
        OLD.radius_m, OLD.restriction_mode, OLD.starts_at, OLD.ends_at, OLD.created_by_id)
       IS DISTINCT FROM
       (NEW.zone_key, NEW.version, NEW.name, NEW.center_latitude, NEW.center_longitude,
        NEW.radius_m, NEW.restriction_mode, NEW.starts_at, NEW.ends_at, NEW.created_by_id)
       OR NOT ((OLD.status = 'DRAFT' AND NEW.status = 'PUBLISHED')
            OR (OLD.status = 'PUBLISHED' AND NEW.status = 'RETIRED')) THEN
      RAISE EXCEPTION 'Immutable zone revision / invalid lifecycle' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_zone_identity_guard BEFORE INSERT OR UPDATE OR DELETE ON cow_servicezone
  FOR EACH ROW EXECUTE FUNCTION cow_zone_identity_guard();

CREATE FUNCTION cow_zone_assignment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE id_value uuid; conflicted boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Zone assignment history cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF (SELECT plane FROM cow_principal WHERE id = NEW.authorized_by_id) IS DISTINCT FROM 'admin'
      OR NEW.authorization_ref IS NULL OR btrim(NEW.authorization_ref) = '' THEN
    RAISE EXCEPTION 'Zone assignment requires Admin authorization' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    -- The only permitted mutation is closing an open interval without
    -- rewriting earlier assignments or backdating the closure.
    IF OLD.ends_at IS NOT NULL OR NEW.ends_at IS NULL
       OR (OLD.zone_id, OLD.starts_at, OLD.authorized_by_id, OLD.authorization_ref)
          IS DISTINCT FROM
          (NEW.zone_id, NEW.starts_at, NEW.authorized_by_id, NEW.authorization_ref)
       OR NEW.ends_at < statement_timestamp() THEN
      RAISE EXCEPTION 'Only forward closing of open assignment is permitted' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'cow_dutyzoneassignment' THEN
      IF OLD.duty_id IS DISTINCT FROM NEW.duty_id THEN
        RAISE EXCEPTION 'Duty assignment owner immutable' USING ERRCODE = '23514';
      END IF;
    ELSE
      IF OLD.charger_id IS DISTINCT FROM NEW.charger_id THEN
        RAISE EXCEPTION 'Charger assignment owner immutable' USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;
  IF TG_TABLE_NAME = 'cow_dutyzoneassignment' THEN
    id_value := NEW.duty_id;
    PERFORM pg_advisory_xact_lock(hashtextextended('cow-duty-zone:' || id_value::text, 0));
    SELECT EXISTS (
      SELECT 1 FROM cow_dutyzoneassignment x
      WHERE x.duty_id = NEW.duty_id AND x.id <> NEW.id
        AND tstzrange(x.starts_at, x.ends_at, '[)') && tstzrange(NEW.starts_at, NEW.ends_at, '[)')
    ) INTO conflicted;
  ELSE
    id_value := NEW.charger_id;
    PERFORM pg_advisory_xact_lock(hashtextextended('cow-charger-zone:' || id_value::text, 0));
    SELECT EXISTS (
      SELECT 1 FROM cow_chargerzoneassignment x
      WHERE x.charger_id = NEW.charger_id AND x.id <> NEW.id
        AND tstzrange(x.starts_at, x.ends_at, '[)') && tstzrange(NEW.starts_at, NEW.ends_at, '[)')
    ) INTO conflicted;
  END IF;
  IF conflicted THEN
    RAISE EXCEPTION 'Overlapping zone assignments' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_duty_zone_guard BEFORE INSERT OR UPDATE OR DELETE ON cow_dutyzoneassignment
  FOR EACH ROW EXECUTE FUNCTION cow_zone_assignment_guard();
CREATE TRIGGER cow_charger_zone_guard BEFORE INSERT OR UPDATE OR DELETE ON cow_chargerzoneassignment
  FOR EACH ROW EXECUTE FUNCTION cow_zone_assignment_guard();

CREATE FUNCTION cow_zone_gst_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Zone GST history cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF (SELECT plane FROM cow_principal WHERE id = NEW.authorized_by_id) IS DISTINCT FROM 'admin'
    OR btrim(NEW.authorization_ref) = ''
    OR btrim(NEW.seller_tax_ref) = '' OR btrim(NEW.legal_decision_ref) = ''
    OR jsonb_typeof(NEW.tax_profiles) IS DISTINCT FROM 'object'
    OR NEW.tax_profiles = '{}'::jsonb THEN
    RAISE EXCEPTION 'Unapproved or structurally incomplete Zone GST decision' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status <> 'DRAFT' THEN
    RAISE EXCEPTION 'Zone GST starts as DRAFT' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF (OLD.zone_id, OLD.channel, OLD.binding_version, OLD.starts_at,
        OLD.seller_tax_ref, OLD.legal_decision_ref, OLD.tax_profiles, OLD.authorized_by_id,
        OLD.authorization_ref) IS DISTINCT FROM
       (NEW.zone_id, NEW.channel, NEW.binding_version, NEW.starts_at,
        NEW.seller_tax_ref, NEW.legal_decision_ref, NEW.tax_profiles, NEW.authorized_by_id,
        NEW.authorization_ref) OR NOT (
        (OLD.status = 'DRAFT' AND NEW.status = 'APPROVED' AND NEW.ends_at IS NOT DISTINCT FROM OLD.ends_at)
        OR (OLD.status = 'APPROVED' AND NEW.status = 'APPROVED'
            AND OLD.ends_at IS NULL AND NEW.ends_at IS NOT NULL
            AND NEW.ends_at >= statement_timestamp())
    ) THEN
      RAISE EXCEPTION 'Immutable GST decision / invalid lifecycle' USING ERRCODE = '23514';
    END IF;
  END IF;
  IF NEW.status = 'APPROVED' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended(
      'cow-zone-gst:' || NEW.zone_id::text || ':' || NEW.channel, 0));
    IF EXISTS (
      SELECT 1 FROM cow_zonegstassignment x
      WHERE x.id <> NEW.id AND x.zone_id = NEW.zone_id
        AND x.channel = NEW.channel AND x.status = 'APPROVED'
        AND tstzrange(x.starts_at, x.ends_at, '[)') && tstzrange(NEW.starts_at, NEW.ends_at, '[)')
    ) THEN
      RAISE EXCEPTION 'Overlapping approved Zone GST decisions' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_zone_gst_guard BEFORE INSERT OR UPDATE OR DELETE ON cow_zonegstassignment
  FOR EACH ROW EXECUTE FUNCTION cow_zone_gst_guard();

CREATE FUNCTION cow_zone_tariff_payload_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  existing_policy cow_pricingpolicy%ROWTYPE;
  owning_zone cow_servicezone%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Zone tariff payload cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'Zone tariff payload is immutable' USING ERRCODE = '23514';
  END IF;
  IF (SELECT plane FROM cow_principal WHERE id = NEW.authorized_by_id) IS DISTINCT FROM 'admin' THEN
    RAISE EXCEPTION 'Tariff payload needs Admin author' USING ERRCODE = '23514';
  END IF;
  SELECT * INTO existing_policy FROM cow_pricingpolicy WHERE id = NEW.policy_id FOR UPDATE;
  SELECT * INTO owning_zone FROM cow_servicezone WHERE id = NEW.zone_id;
  IF NOT FOUND OR existing_policy.id IS NULL OR existing_policy.status <> 'DRAFT'
     OR existing_policy.scope ->> 'kind' IS DISTINCT FROM 'ZONE_' || NEW.channel
     OR existing_policy.scope ->> 'key' IS DISTINCT FROM owning_zone.zone_key
     OR existing_policy.policy_key IS DISTINCT FROM NEW.plan ->> 'policy_id'
     OR jsonb_typeof(NEW.plan -> 'policy_version') IS DISTINCT FROM 'number'
     OR (NEW.plan ->> 'policy_version') IS DISTINCT FROM existing_policy.policy_version::text
     OR NEW.plan ->> 'zone_id' IS DISTINCT FROM owning_zone.zone_key
     OR NEW.plan ->> 'channel' IS DISTINCT FROM NEW.channel THEN
    RAISE EXCEPTION 'Tariff payload is not attached to a matching draft policy and zone'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_zone_tariff_payload_guard BEFORE INSERT OR UPDATE OR DELETE ON cow_zonetariffpayload
  FOR EACH ROW EXECUTE FUNCTION cow_zone_tariff_payload_guard();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS cow_zone_tariff_payload_guard ON cow_zonetariffpayload;
DROP FUNCTION IF EXISTS cow_zone_tariff_payload_guard();
DROP TRIGGER IF EXISTS cow_zone_gst_guard ON cow_zonegstassignment;
DROP FUNCTION IF EXISTS cow_zone_gst_guard();
DROP TRIGGER IF EXISTS cow_duty_zone_guard ON cow_dutyzoneassignment;
DROP TRIGGER IF EXISTS cow_charger_zone_guard ON cow_chargerzoneassignment;
DROP FUNCTION IF EXISTS cow_zone_assignment_guard();
DROP TRIGGER IF EXISTS cow_zone_identity_guard ON cow_servicezone;
DROP FUNCTION IF EXISTS cow_zone_identity_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("cow", "0007_rbac_pricing_consequential_parity")]
    operations = [
        migrations.CreateModel(
            name="ServiceZone",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, default=uuid.uuid4, editable=False)),
                ("zone_key", models.CharField(max_length=160)),
                ("version", models.PositiveIntegerField()),
                ("name", models.CharField(max_length=160)),
                ("center_latitude", models.DecimalField(max_digits=10, decimal_places=7)),
                ("center_longitude", models.DecimalField(max_digits=10, decimal_places=7)),
                ("radius_m", models.PositiveIntegerField()),
                ("restriction_mode", models.CharField(max_length=8, choices=[("STRICT", "STRICT"), ("ADVISORY", "ADVISORY")])),
                ("status", models.CharField(max_length=12, choices=[("DRAFT", "DRAFT"), ("PUBLISHED", "PUBLISHED"), ("RETIRED", "RETIRED")], default="DRAFT")),
                ("starts_at", models.DateTimeField(null=True, blank=True)),
                ("ends_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(to="cow.principal", related_name="authored_service_zones", on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("zone_key", "version"), name="cow_zone_key_revision_unique"),
                models.CheckConstraint(condition=Q(version__gt=0), name="cow_zone_version_positive"),
                models.CheckConstraint(condition=Q(radius_m__gt=0), name="cow_zone_radius_positive"),
                models.CheckConstraint(condition=Q(center_latitude__gte=-90) & Q(center_latitude__lte=90), name="cow_zone_lat_range"),
                models.CheckConstraint(condition=Q(center_longitude__gte=-180) & Q(center_longitude__lte=180), name="cow_zone_lon_range"),
                models.CheckConstraint(condition=Q(ends_at__isnull=True) | (Q(starts_at__isnull=False) & Q(ends_at__gt=F("starts_at"))), name="cow_zone_valid_window"),
            ]},
        ),
        migrations.CreateModel(
            name="DutyZoneAssignment",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, default=uuid.uuid4, editable=False)),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField(null=True, blank=True)),
                ("authorization_ref", models.CharField(max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("duty", models.ForeignKey(to="cow.dutysession", related_name="zone_assignments", on_delete=django.db.models.deletion.PROTECT)),
                ("zone", models.ForeignKey(to="cow.servicezone", related_name="duty_assignments", on_delete=django.db.models.deletion.PROTECT)),
                ("authorized_by", models.ForeignKey(to="cow.principal", related_name="authorized_duty_zones", on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("duty", "starts_at"), name="cow_duty_zone_start_unique"),
                models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="cow_duty_zone_valid_window"),
            ]},
        ),
        migrations.CreateModel(
            name="ChargerZoneAssignment",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, default=uuid.uuid4, editable=False)),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField(null=True, blank=True)),
                ("authorization_ref", models.CharField(max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("charger", models.ForeignKey(to="cow.charger", related_name="zone_assignments", on_delete=django.db.models.deletion.PROTECT)),
                ("zone", models.ForeignKey(to="cow.servicezone", related_name="charger_assignments", on_delete=django.db.models.deletion.PROTECT)),
                ("authorized_by", models.ForeignKey(to="cow.principal", related_name="authorized_charger_zones", on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("charger", "starts_at"), name="cow_charger_zone_start_unique"),
                models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="cow_charger_zone_valid_window"),
            ]},
        ),
        migrations.CreateModel(
            name="ZoneGSTAssignment",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, default=uuid.uuid4, editable=False)),
                ("channel", models.CharField(max_length=12, choices=[("ROAMING", "ROAMING"), ("ON_SITE_QR", "ON_SITE_QR")])),
                ("binding_version", models.PositiveIntegerField()),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField(null=True, blank=True)),
                ("seller_tax_ref", models.CharField(max_length=200)),
                ("legal_decision_ref", models.CharField(max_length=200)),
                ("tax_profiles", models.JSONField()),
                ("status", models.CharField(max_length=10, choices=[("DRAFT", "DRAFT"), ("APPROVED", "APPROVED")], default="DRAFT")),
                ("authorization_ref", models.CharField(max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("zone", models.ForeignKey(to="cow.servicezone", related_name="gst_assignments", on_delete=django.db.models.deletion.PROTECT)),
                ("authorized_by", models.ForeignKey(to="cow.principal", related_name="approved_zone_gst", on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("zone", "channel", "binding_version"), name="cow_zone_gst_version_unique"),
                models.CheckConstraint(condition=Q(binding_version__gt=0), name="cow_zone_gst_ver_positive"),
                models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="cow_zone_gst_valid_window"),
            ]},
        ),
        migrations.CreateModel(
            name="ZoneTariffPayload",
            fields=[
                ("policy", models.OneToOneField(to="cow.pricingpolicy", primary_key=True, serialize=False, related_name="zone_payload", on_delete=django.db.models.deletion.PROTECT)),
                ("channel", models.CharField(max_length=12, choices=[("ROAMING", "ROAMING"), ("ON_SITE_QR", "ON_SITE_QR")])) ,
                ("plan", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("zone", models.ForeignKey(to="cow.servicezone", related_name="tariff_payloads", on_delete=django.db.models.deletion.PROTECT)),
                ("authorized_by", models.ForeignKey(to="cow.principal", related_name="authored_zone_tariff_payloads", on_delete=django.db.models.deletion.PROTECT)),
            ],
        ),
        migrations.RunSQL(FORWARD, reverse_sql=REVERSE),
    ]
