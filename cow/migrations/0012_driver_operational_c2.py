"""C2 positive clearance/versioning and immutable operational decision history.

No automatic promotion from PENDING, no Principal/session changes and no data backfill.
Reverse refuses to discard historical decisions.
"""
import uuid

from django.db import migrations, models
from django.db.models import Q
from django.db.migrations.exceptions import IrreversibleError
import django.db.models.deletion


def refuse_history_loss(apps, schema_editor):
    Decision = apps.get_model("cow", "DriverOperationalDecision")
    if Decision.objects.using(schema_editor.connection.alias).exists():
        raise IrreversibleError(
            "C2 history exists: cannot reverse without losing immutable operational decisions"
        )


class Migration(migrations.Migration):
    dependencies = [("cow", "0011_driver_recruitment_c1")]
    operations = [
        migrations.AddField(
            model_name="driverprofile", name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddConstraint(
            model_name="driverprofile",
            constraint=models.CheckConstraint(
                condition=Q(eligibility__in=["PENDING", "ACTIVE", "SUSPENDED", "OFFBOARDED"]),
                name="cow_driver_oper_eligibility_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="driverprofile",
            constraint=models.CheckConstraint(condition=Q(version__gte=1),
                                              name="cow_driver_oper_version_positive"),
        ),
        migrations.CreateModel(
            name="DriverOperationalDecision",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4,
                                        editable=False, serialize=False)),
                ("action", models.CharField(max_length=16)),
                ("from_status", models.CharField(max_length=24)),
                ("to_status", models.CharField(max_length=24)),
                ("reason", models.CharField(max_length=500)),
                ("result_version", models.PositiveIntegerField()),
                ("application_version", models.PositiveIntegerField(null=True)),
                ("licence_valid_until", models.DateField(null=True)),
                ("evidence_digest", models.CharField(max_length=64, null=True)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("driver", models.ForeignKey(to="cow.driverprofile",
                                             on_delete=django.db.models.deletion.PROTECT,
                                             related_name="operational_decisions")),
                ("actor", models.ForeignKey(to="cow.principal",
                                            on_delete=django.db.models.deletion.PROTECT)),
                ("application", models.ForeignKey(to="cow.driverapplication", null=True,
                                                  on_delete=django.db.models.deletion.PROTECT)),
                ("policy", models.ForeignKey(to="cow.driverrecruitmentpolicy", null=True,
                                             on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={
                "indexes": [models.Index(fields=["driver", "-occurred_at", "-id"],
                                         name="cow_driver_oper_history_idx")],
                "constraints": [models.UniqueConstraint(
                    fields=["driver", "result_version"],
                    name="cow_driver_oper_one_decision_version",
                )],
            },
        ),
        migrations.RunSQL(
            sql=r"""
-- Newly inserted DriverProfiles can only begin pending. Operational ACTIVE
-- must be earned through the versioned decision transaction, not a direct INSERT.
CREATE FUNCTION cow_driver_c2_profile_initial_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.eligibility IS DISTINCT FROM 'PENDING' OR NEW.version IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'New Driver profile must start PENDING at version 1'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_driver_c2_profile_insert_guard
BEFORE INSERT ON cow_driverprofile
FOR EACH ROW EXECUTE FUNCTION cow_driver_c2_profile_initial_guard();

CREATE FUNCTION cow_driver_c2_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Driver operational decisions are append-only' USING ERRCODE='23514';
END $$;
CREATE TRIGGER cow_driver_c2_history_guard
BEFORE UPDATE OR DELETE ON cow_driveroperationaldecision
FOR EACH ROW EXECUTE FUNCTION cow_driver_c2_history_immutable();

-- A direct UPDATE without its matching durable decision must not be able to
-- silently promote, suspend or offboard a Driver. Deferred until commit so
-- service code can update the profile and insert decision in one transaction.
CREATE FUNCTION cow_driver_c2_profile_decision_required() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.eligibility IS DISTINCT FROM OLD.eligibility
     OR NEW.version IS DISTINCT FROM OLD.version THEN
    IF NEW.eligibility IS NOT DISTINCT FROM OLD.eligibility
       OR NEW.version <> OLD.version + 1 THEN
      RAISE EXCEPTION 'Driver operational status/version must transition together'
        USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM cow_driveroperationaldecision d
      WHERE d.driver_id=NEW.id AND d.result_version=NEW.version
        AND d.from_status=OLD.eligibility AND d.to_status=NEW.eligibility
    ) THEN
      RAISE EXCEPTION 'Driver operational transition requires durable decision'
        USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER cow_driver_c2_profile_guard
AFTER UPDATE ON cow_driverprofile
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION cow_driver_c2_profile_decision_required();
""",
            reverse_sql=r"""
DROP TRIGGER IF EXISTS cow_driver_c2_profile_insert_guard ON cow_driverprofile;
DROP FUNCTION IF EXISTS cow_driver_c2_profile_initial_guard();
DROP TRIGGER IF EXISTS cow_driver_c2_profile_guard ON cow_driverprofile;
DROP FUNCTION IF EXISTS cow_driver_c2_profile_decision_required();
DROP TRIGGER IF EXISTS cow_driver_c2_history_guard ON cow_driveroperationaldecision;
DROP FUNCTION IF EXISTS cow_driver_c2_history_immutable();
""",
        ),
        migrations.RunPython(migrations.RunPython.noop, refuse_history_loss),
    ]
