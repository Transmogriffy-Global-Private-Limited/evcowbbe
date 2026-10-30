"""Add Go-compatible vehicle metadata and durable maintenance history."""

import uuid

from django.db import migrations, models
from django.db.models import F, Q

FORWARD = r"""
CREATE FUNCTION cow_vehicle_maintenance_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Locking the parent serializes concurrent maintenance inserts for one asset.
    PERFORM 1 FROM cow_servicevehicle WHERE id = NEW.vehicle_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM cow_vehiclemaintenanceperiod
        WHERE vehicle_id = NEW.vehicle_id
          AND starts_at < NEW.ends_at
          AND ends_at > NEW.starts_at
    ) THEN
        RAISE EXCEPTION 'Vehicle maintenance periods must not overlap' USING ERRCODE = '23P01';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cow_vehicle_maintenance_no_overlap
    BEFORE INSERT ON cow_vehiclemaintenanceperiod
    FOR EACH ROW EXECUTE FUNCTION cow_vehicle_maintenance_guard();

CREATE FUNCTION cow_vehicle_maintenance_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Vehicle maintenance history is append-only' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER cow_vehicle_maintenance_immutable
    BEFORE UPDATE OR DELETE ON cow_vehiclemaintenanceperiod
    FOR EACH ROW EXECUTE FUNCTION cow_vehicle_maintenance_append_only();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS cow_vehicle_maintenance_immutable ON cow_vehiclemaintenanceperiod;
DROP FUNCTION IF EXISTS cow_vehicle_maintenance_append_only();
DROP TRIGGER IF EXISTS cow_vehicle_maintenance_no_overlap ON cow_vehiclemaintenanceperiod;
DROP FUNCTION IF EXISTS cow_vehicle_maintenance_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("cow", "0002_business_invariants")]

    operations = [
        migrations.AlterField(
            model_name="servicevehicle",
            name="registration_number",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="servicevehicle",
            name="inspection_status",
            field=models.CharField(
                choices=[(value, value) for value in ("UNKNOWN", "VALID", "FAILED", "EXPIRED")],
                default="UNKNOWN",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="servicevehicle",
            name="insurance_valid_until",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="servicevehicle",
            name="notes",
            field=models.CharField(blank=True, max_length=4000, null=True),
        ),
        migrations.AddField(
            model_name="servicevehicle",
            name="ownership_type",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="servicevehicle",
            name="registration_valid_until",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="servicevehicle",
            name="variant",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="servicevehicle",
            name="vehicle_type",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddConstraint(
            model_name="servicevehicle",
            constraint=models.CheckConstraint(
                condition=~Q(operational_status="RETIRED") | Q(active=False),
                name="cow_vehicle_retired_inactive",
            ),
        ),
        migrations.CreateModel(
            name="VehicleMaintenancePeriod",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField()),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(null=True, on_delete=models.SET_NULL, to="cow.principal"),
                ),
                ("vehicle", models.ForeignKey(on_delete=models.PROTECT, to="cow.servicevehicle")),
            ],
        ),
        migrations.AddConstraint(
            model_name="vehiclemaintenanceperiod",
            constraint=models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="cow_vehicle_maintenance_range"
            ),
        ),
        migrations.AddIndex(
            model_name="vehiclemaintenanceperiod",
            index=models.Index(
                fields=["vehicle", "starts_at", "ends_at"], name="cow_vehicle_maint_window"
            ),
        ),
        migrations.RunSQL(FORWARD, reverse_sql=REVERSE),
    ]
