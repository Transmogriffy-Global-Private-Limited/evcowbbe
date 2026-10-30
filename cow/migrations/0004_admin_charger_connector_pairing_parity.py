"""Add Go-compatible charger, connector, pairing, and maintenance invariants."""

import uuid

from django.db import migrations, models
from django.db.models import F, Q
from django.utils import timezone

FORWARD = r"""
CREATE FUNCTION cow_charger_maintenance_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Serialize concurrent intervals for one charger before checking overlap.
    PERFORM 1 FROM cow_charger WHERE id = NEW.charger_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM cow_chargermaintenanceperiod
        WHERE charger_id = NEW.charger_id
          AND starts_at < NEW.ends_at
          AND ends_at > NEW.starts_at
    ) THEN
        RAISE EXCEPTION 'Charger maintenance periods must not overlap' USING ERRCODE = '23P01';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cow_charger_maintenance_no_overlap
    BEFORE INSERT ON cow_chargermaintenanceperiod
    FOR EACH ROW EXECUTE FUNCTION cow_charger_maintenance_guard();

CREATE FUNCTION cow_charger_maintenance_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Charger maintenance history is append-only' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER cow_charger_maintenance_immutable
    BEFORE UPDATE OR DELETE ON cow_chargermaintenanceperiod
    FOR EACH ROW EXECUTE FUNCTION cow_charger_maintenance_append_only();

CREATE FUNCTION cow_pairing_closed_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'ACTIVE' THEN
        RAISE EXCEPTION 'Closed pairing history is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.vehicle_id IS DISTINCT FROM OLD.vehicle_id
       OR NEW.charger_id IS DISTINCT FROM OLD.charger_id
       OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
       OR NEW.created_by_id IS DISTINCT FROM OLD.created_by_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.expected IS DISTINCT FROM OLD.expected THEN
        RAISE EXCEPTION 'Pairing identity is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'ACTIVE' OR NEW.effective_to IS NULL THEN
        RAISE EXCEPTION 'Pairing must transition out of ACTIVE once' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cow_pairing_lifecycle_guard
    BEFORE UPDATE ON cow_vehiclechargerpairing
    FOR EACH ROW EXECUTE FUNCTION cow_pairing_closed_immutable();

CREATE UNIQUE INDEX cow_one_active_pairing_per_charger
    ON cow_vehiclechargerpairing(charger_id) WHERE status = 'ACTIVE';
"""

REVERSE = r"""
DROP INDEX IF EXISTS cow_one_active_pairing_per_charger;
DROP TRIGGER IF EXISTS cow_pairing_lifecycle_guard ON cow_vehiclechargerpairing;
DROP FUNCTION IF EXISTS cow_pairing_closed_immutable();
DROP TRIGGER IF EXISTS cow_charger_maintenance_immutable ON cow_chargermaintenanceperiod;
DROP FUNCTION IF EXISTS cow_charger_maintenance_append_only();
DROP TRIGGER IF EXISTS cow_charger_maintenance_no_overlap ON cow_chargermaintenanceperiod;
DROP FUNCTION IF EXISTS cow_charger_maintenance_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("cow", "0003_admin_fleet_vehicle_parity")]

    operations = [
        migrations.AddField(
            model_name="charger",
            name="maintenance_status",
            field=models.CharField(default="UNKNOWN", max_length=24),
        ),
        migrations.AddField(
            model_name="charger",
            name="notes",
            field=models.CharField(blank=True, max_length=4000, null=True),
        ),
        migrations.AddField(
            model_name="charger",
            name="protocol_family",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="charger",
            name="protocol_version",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="charger",
            name="serial_number",
            field=models.CharField(blank=True, max_length=120, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name="charger",
            name="hal_identity",
            field=models.CharField(blank=True, max_length=150, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name="charger",
            name="rated_power_w",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="charger",
            constraint=models.CheckConstraint(
                condition=~Q(operational_status="RETIRED") | Q(active=False),
                name="cow_charger_retired_inactive",
            ),
        ),
        migrations.AddField(
            model_name="chargerconnector",
            name="active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="chargerconnector",
            name="supports_ac",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="chargerconnector",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AlterField(
            model_name="chargerconnector",
            name="connector_number",
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterField(
            model_name="chargerconnector",
            name="connector_type",
            field=models.CharField(max_length=64),
        ),
        migrations.AddConstraint(
            model_name="chargerconnector",
            constraint=models.CheckConstraint(
                condition=Q(supports_ac=True) | Q(supports_dc=True),
                name="cow_connector_has_current",
            ),
        ),
        migrations.AddField(
            model_name="vehiclechargerpairing",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="vehiclechargerpairing",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="created_pairings",
                to="cow.principal",
            ),
        ),
        migrations.AddField(
            model_name="vehiclechargerpairing",
            name="end_reason",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="vehiclechargerpairing",
            name="ended_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="ended_pairings",
                to="cow.principal",
            ),
        ),
        migrations.AddField(
            model_name="vehiclechargerpairing",
            name="reason",
            field=models.CharField(default="Legacy pairing record", max_length=500),
        ),
        migrations.AddField(
            model_name="vehiclechargerpairing",
            name="source",
            field=models.CharField(
                choices=[("ADMIN", "ADMIN"), ("RECONCILIATION", "RECONCILIATION")],
                default="ADMIN",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="vehiclechargerpairing",
            name="status",
            field=models.CharField(
                choices=[("ACTIVE", "ACTIVE"), ("ENDED", "ENDED"), ("INVALIDATED", "INVALIDATED")],
                default="ACTIVE",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="vehiclechargerpairing",
            name="effective_to",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="vehiclechargerpairing",
            constraint=models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gt=F("effective_from")),
                name="cow_pairing_time_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="vehiclechargerpairing",
            constraint=models.CheckConstraint(
                condition=(
                    Q(status="ACTIVE", effective_to__isnull=True)
                    | (~Q(status="ACTIVE") & Q(effective_to__isnull=False))
                ),
                name="cow_pairing_lifecycle",
            ),
        ),
        migrations.CreateModel(
            name="ChargerMaintenancePeriod",
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
                    "charger",
                    models.ForeignKey(on_delete=models.PROTECT, to="cow.charger"),
                ),
                (
                    "created_by",
                    models.ForeignKey(null=True, on_delete=models.SET_NULL, to="cow.principal"),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="chargermaintenanceperiod",
            constraint=models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="cow_charger_maintenance_range",
            ),
        ),
        migrations.AddIndex(
            model_name="chargermaintenanceperiod",
            index=models.Index(
                fields=["charger", "starts_at", "ends_at"], name="cow_charger_maint_window"
            ),
        ),
        migrations.RunSQL(FORWARD, reverse_sql=REVERSE),
    ]
