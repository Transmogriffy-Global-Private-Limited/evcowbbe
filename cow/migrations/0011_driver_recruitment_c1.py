"""C1 append-only recruitment, verified document and policy metadata.

No backfill, no Driver status promotion, no filesystem writes during migration.
"""
import uuid
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("cow", "0010_commercial_machine_registry")]
    operations = [
        migrations.CreateModel(
            name="DriverRecruitmentPolicy",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("policy_key", models.CharField(max_length=64, default="DEFAULT")),
                ("version", models.PositiveIntegerField()),
                ("required_document_types", models.JSONField(default=list)),
                ("scan_required", models.BooleanField(default=True)),
                ("legal_decision_ref", models.CharField(max_length=200)),
                ("active", models.BooleanField(default=False)),
                ("created_by", models.ForeignKey(to="cow.principal", on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=["policy_key", "version"], name="cow_driver_policy_version_unique"),
                models.UniqueConstraint(fields=["policy_key"], condition=Q(active=True), name="cow_driver_one_active_policy"),
                models.CheckConstraint(condition=Q(version__gte=1), name="cow_driver_policy_positive_version"),
            ]},
        ),
        migrations.CreateModel(
            name="DriverApplication",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("state", models.CharField(max_length=24, default="DRAFT")),
                ("version", models.PositiveIntegerField(default=1)),
                ("legal_name", models.CharField(max_length=160, default="", blank=True)),
                ("contact_phone", models.CharField(max_length=32, default="", blank=True)),
                ("policy", models.ForeignKey(to="cow.driverrecruitmentpolicy", null=True, on_delete=django.db.models.deletion.PROTECT)),
                ("submitted_at", models.DateTimeField(null=True)),
                ("reviewed_at", models.DateTimeField(null=True)),
                ("principal", models.OneToOneField(to="cow.principal", on_delete=django.db.models.deletion.PROTECT)),
            ],
        ),
        migrations.CreateModel(
            name="DriverDocument",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("document_type", models.CharField(max_length=64)),
                ("storage_key", models.CharField(max_length=100, unique=True)),
                ("storage_provider", models.CharField(max_length=24, default="LOCAL")),
                ("content_type", models.CharField(max_length=80)),
                ("original_filename", models.CharField(max_length=255)),
                ("size_bytes", models.PositiveIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("expires_on", models.DateField(null=True)),
                ("review_status", models.CharField(max_length=20, default="PENDING")),
                ("scan_status", models.CharField(max_length=24, default="NOT_SCANNED")),
                ("application", models.ForeignKey(to="cow.driverapplication", on_delete=django.db.models.deletion.PROTECT, related_name="documents")),
                ("uploaded_by", models.ForeignKey(to="cow.principal", on_delete=django.db.models.deletion.PROTECT)),
                ("replaces", models.ForeignKey(to="cow.driverdocument", on_delete=django.db.models.deletion.PROTECT, null=True)),
            ],
            options={"indexes": [models.Index(fields=["application", "document_type", "created_at"], name="cow_driver_doc_latest_idx")]},
        ),
        migrations.CreateModel(
            name="DriverDocumentReview",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("decision", models.CharField(max_length=20)),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(to="cow.principal", on_delete=django.db.models.deletion.PROTECT)),
                ("document", models.ForeignKey(to="cow.driverdocument", on_delete=django.db.models.deletion.PROTECT, related_name="reviews")),
            ],
        ),
        migrations.CreateModel(
            name="DriverApplicationHistory",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("from_state", models.CharField(max_length=24)),
                ("to_state", models.CharField(max_length=24)),
                ("reason", models.CharField(max_length=500)),
                ("event_key", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
                ("delivery_state", models.CharField(max_length=24, default="NOT_CONFIGURED")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(to="cow.principal", on_delete=django.db.models.deletion.PROTECT)),
                ("application", models.ForeignKey(to="cow.driverapplication", on_delete=django.db.models.deletion.PROTECT, related_name="history")),
            ],
        ),
        migrations.RunSQL(
            sql=r"""
CREATE FUNCTION cow_driver_c1_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Driver recruitment evidence is append-only' USING ERRCODE='23514';
END $$;
CREATE TRIGGER cow_driver_review_append_only BEFORE UPDATE OR DELETE ON cow_driverdocumentreview
FOR EACH ROW EXECUTE FUNCTION cow_driver_c1_append_only();
CREATE TRIGGER cow_driver_history_append_only BEFORE UPDATE OR DELETE ON cow_driverapplicationhistory
FOR EACH ROW EXECUTE FUNCTION cow_driver_c1_append_only();
CREATE FUNCTION cow_driver_document_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Driver document cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF NEW.application_id IS DISTINCT FROM OLD.application_id
     OR NEW.document_type IS DISTINCT FROM OLD.document_type
     OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
     OR NEW.storage_provider IS DISTINCT FROM OLD.storage_provider
     OR NEW.content_type IS DISTINCT FROM OLD.content_type
     OR NEW.original_filename IS DISTINCT FROM OLD.original_filename
     OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
     OR NEW.sha256 IS DISTINCT FROM OLD.sha256
     OR NEW.expires_on IS DISTINCT FROM OLD.expires_on
     OR NEW.replaces_id IS DISTINCT FROM OLD.replaces_id
     OR NEW.uploaded_by_id IS DISTINCT FROM OLD.uploaded_by_id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'Driver document evidence cannot be rewritten' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_driver_doc_immutable BEFORE UPDATE OR DELETE ON cow_driverdocument
FOR EACH ROW EXECUTE FUNCTION cow_driver_document_immutable();
CREATE FUNCTION cow_driver_policy_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Recruitment policy cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF NEW.policy_key IS DISTINCT FROM OLD.policy_key OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.required_document_types IS DISTINCT FROM OLD.required_document_types
     OR NEW.scan_required IS DISTINCT FROM OLD.scan_required
     OR NEW.legal_decision_ref IS DISTINCT FROM OLD.legal_decision_ref
     OR NEW.created_by_id IS DISTINCT FROM OLD.created_by_id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'Recruitment policy versions are immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_driver_policy_immutable BEFORE UPDATE OR DELETE ON cow_driverrecruitmentpolicy
FOR EACH ROW EXECUTE FUNCTION cow_driver_policy_immutable();
""",
            reverse_sql=r"""
DROP TRIGGER IF EXISTS cow_driver_policy_immutable ON cow_driverrecruitmentpolicy;
DROP FUNCTION IF EXISTS cow_driver_policy_immutable();
DROP TRIGGER IF EXISTS cow_driver_doc_immutable ON cow_driverdocument;
DROP FUNCTION IF EXISTS cow_driver_document_immutable();
DROP TRIGGER IF EXISTS cow_driver_history_append_only ON cow_driverapplicationhistory;
DROP TRIGGER IF EXISTS cow_driver_review_append_only ON cow_driverdocumentreview;
DROP FUNCTION IF EXISTS cow_driver_c1_append_only();
""",
        ),
    ]
