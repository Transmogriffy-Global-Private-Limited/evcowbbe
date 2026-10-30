"""Additive Customer profile, EV and address fields; preserve historic snapshots."""

import uuid

import django.db.models.deletion
from django.db import migrations, models

FORWARD = r"""
CREATE FUNCTION cow_customer_master_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.owner_id IS DISTINCT FROM NEW.owner_id
            OR NEW.version IS DISTINCT FROM OLD.version + 1
            OR (NOT OLD.active AND NEW.active) THEN
            RAISE EXCEPTION 'Customer asset ownership/version/retirement invariant'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'cow_customeraddress' THEN
        IF NEW.is_default AND (NOT NEW.active OR NOT NEW.saved) THEN
            RAISE EXCEPTION 'Only active saved addresses may be default'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cow_customer_vehicle_guard
  BEFORE INSERT OR UPDATE ON cow_customervehicle
  FOR EACH ROW EXECUTE FUNCTION cow_customer_master_guard();
CREATE TRIGGER cow_customer_address_guard
  BEFORE INSERT OR UPDATE ON cow_customeraddress
  FOR EACH ROW EXECUTE FUNCTION cow_customer_master_guard();

CREATE FUNCTION cow_customer_profile_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT plane FROM cow_principal WHERE id = NEW.principal_id)
        IS DISTINCT FROM 'app' THEN
        RAISE EXCEPTION 'Profile requires app principal' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        OLD.principal_id IS DISTINCT FROM NEW.principal_id
        OR NEW.version IS DISTINCT FROM OLD.version + 1
    ) THEN
        RAISE EXCEPTION 'Customer profile ownership/version invariant'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cow_customer_profile_guard
  BEFORE INSERT OR UPDATE ON cow_customerprofile
  FOR EACH ROW EXECUTE FUNCTION cow_customer_profile_guard();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS cow_customer_profile_guard ON cow_customerprofile;
DROP FUNCTION IF EXISTS cow_customer_profile_guard();
DROP TRIGGER IF EXISTS cow_customer_address_guard ON cow_customeraddress;
DROP TRIGGER IF EXISTS cow_customer_vehicle_guard ON cow_customervehicle;
DROP FUNCTION IF EXISTS cow_customer_master_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("cow", "0005_admin_rbac_pricing_parity")]
    operations = [
        migrations.CreateModel(
            name="CustomerProfile",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False, default=uuid.uuid4)),
                ("full_name", models.CharField(max_length=160, blank=True)),
                ("phone", models.CharField(max_length=24, blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("principal", models.OneToOneField(to="cow.principal", on_delete=django.db.models.deletion.PROTECT, related_name="customer_profile")),
            ],
        ),
        migrations.AddField(model_name="customervehicle", name="variant", field=models.CharField(max_length=120, blank=True)),
        migrations.AddField(model_name="customervehicle", name="battery_capacity_wh", field=models.PositiveBigIntegerField(null=True, blank=True)),
        migrations.AddField(model_name="customeraddress", name="line1", field=models.CharField(max_length=250, blank=True)),
        migrations.AddField(model_name="customeraddress", name="line2", field=models.CharField(max_length=250, blank=True)),
        migrations.AddField(model_name="customeraddress", name="locality", field=models.CharField(max_length=150, blank=True)),
        migrations.AddField(model_name="customeraddress", name="city", field=models.CharField(max_length=120, blank=True)),
        migrations.AddField(model_name="customeraddress", name="state", field=models.CharField(max_length=120, blank=True)),
        migrations.AddField(model_name="customeraddress", name="postal_code", field=models.CharField(max_length=24, blank=True)),
        migrations.AddField(model_name="customeraddress", name="country_code", field=models.CharField(max_length=2, default="IN")),
        migrations.AddField(model_name="customeraddress", name="landmark", field=models.CharField(max_length=160, blank=True)),
        migrations.AddField(model_name="customeraddress", name="floor", field=models.CharField(max_length=60, blank=True)),
        migrations.AddField(model_name="customeraddress", name="contact_instructions", field=models.CharField(max_length=500, blank=True)),
        migrations.AddField(model_name="customeraddress", name="geocode_provider", field=models.CharField(max_length=80, blank=True)),
        migrations.AddField(model_name="customeraddress", name="geocode_provider_place_id", field=models.CharField(max_length=200, blank=True)),
        migrations.RunSQL(FORWARD, reverse_sql=REVERSE),
    ]
