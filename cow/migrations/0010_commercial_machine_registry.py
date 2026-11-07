"""Forward-only extension of Sprint 7: reusable tariff/GST library and assignments.

Do NOT run against a shared development database without reviewing the exact
migration plan and checking migration state. No legacy Zone tariff/GST decisions
are silently converted, withdrawn, inferred or deleted.
"""
import uuid
from importlib import import_module

from django.db import migrations, models
from django.db.models import Q, F
import django.db.models.deletion

BASE = import_module('cow.migrations.0009_zone_admin_operations').FORWARD
OLD = "OR (jsonb_array_length(NEW.rules) = 0 AND NEW.scope->>'kind' NOT IN ('ZONE_ROAMING', 'ZONE_ON_SITE_QR'))"
NEW = "OR (jsonb_array_length(NEW.rules) = 0 AND NEW.scope->>'kind' NOT IN ('ZONE_ROAMING', 'ZONE_ON_SITE_QR', 'LIBRARY_COMPOSITE'))"
ANCHOR = '    RETURN NEW;\nEND $$;'
assert BASE.count(OLD) == 1 and BASE.count(ANCHOR) == 1, 'Policy DB guard changed: manual migration review required'
GUARD = r"""
    IF NEW.policy_key IS NOT NULL AND NEW.scope->>'kind' = 'LIBRARY_COMPOSITE' THEN
        IF NEW.rules IS DISTINCT FROM '[]'::jsonb OR NEW.scope->>'key' IS DISTINCT FROM NEW.policy_key || ':' || NEW.policy_version::text THEN
            RAISE EXCEPTION 'Library tariff cannot masquerade as generic pricing' USING ERRCODE='23514';
        END IF;
        IF NEW.status = 'PUBLISHED' AND NOT EXISTS (
            SELECT 1 FROM cow_commercialtariffpayload x
            WHERE x.policy_id = NEW.id AND x.plan->>'key'=NEW.policy_key
              AND x.plan->>'version'=NEW.policy_version::text
              AND x.plan->>'channel'=x.channel AND x.plan->>'currency'='INR'
        ) THEN
            RAISE EXCEPTION 'Published reusable tariff requires matching immutable payload' USING ERRCODE='23514';
        END IF;
    END IF;
"""
FORWARD_GUARD = BASE.replace(OLD,NEW).replace(ANCHOR,GUARD+ANCHOR)

OTHER_GUARDS = r"""
-- Friendly group names are unique without case/whitespace ambiguities.
CREATE UNIQUE INDEX cow_cm_group_name_normalized ON cow_commercialpricinggroup (lower(btrim(name)));

-- The reusable payload is immutable and only attaches to a matching draft.
CREATE FUNCTION cow_cm_payload_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p cow_pricingpolicy%ROWTYPE;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'Published tariff payloads are append-only' USING ERRCODE='23514';
  END IF;
  SELECT * INTO p FROM cow_pricingpolicy WHERE id=NEW.policy_id FOR UPDATE;
  IF p.id IS NULL OR p.status <> 'DRAFT' OR p.scope->>'kind' <> 'LIBRARY_COMPOSITE'
     OR p.policy_key <> NEW.plan->>'key' OR p.policy_version::text <> NEW.plan->>'version'
     OR NEW.channel <> NEW.plan->>'channel' OR NEW.plan->>'currency' <> 'INR'
     OR (SELECT plane FROM cow_principal WHERE id=NEW.created_by_id) <> 'admin' THEN
    RAISE EXCEPTION 'Tariff payload mismatches draft publication ledger' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_payload_immutable BEFORE INSERT OR UPDATE OR DELETE ON cow_commercialtariffpayload
FOR EACH ROW EXECUTE FUNCTION cow_cm_payload_guard();

-- Versioned GST approvals are frozen. Approval identity cannot be inferred.
CREATE FUNCTION cow_cm_gst_profile_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'GST profile version cannot be deleted' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' THEN
    IF (OLD.profile_key, OLD.profile_version, OLD.seller_tax_ref, OLD.legal_decision_ref,
        OLD.tax_classes, OLD.created_by_id)
       IS DISTINCT FROM
       (NEW.profile_key, NEW.profile_version, NEW.seller_tax_ref, NEW.legal_decision_ref,
        NEW.tax_classes, NEW.created_by_id) THEN
       RAISE EXCEPTION 'GST version is immutable; create a new draft' USING ERRCODE='23514';
    END IF;
    IF NOT ((OLD.status='DRAFT' AND NEW.status='APPROVED' AND NEW.approved_by_id IS NOT NULL)
        OR (OLD.status='APPROVED' AND NEW.status IN ('RETIRED','REVOKED'))
        OR (OLD.status=NEW.status AND OLD.approved_by_id IS NOT DISTINCT FROM NEW.approved_by_id)) THEN
       RAISE EXCEPTION 'Invalid GST status transition' USING ERRCODE='23514';
    END IF;
    IF OLD.status <> 'DRAFT' AND OLD.approved_by_id IS DISTINCT FROM NEW.approved_by_id THEN
       RAISE EXCEPTION 'GST approval provenance is immutable' USING ERRCODE='23514';
    END IF;
  END IF;
  IF NEW.status = 'APPROVED' AND (
      NEW.approved_by_id IS NULL OR btrim(NEW.legal_decision_ref) = '' OR
      btrim(NEW.seller_tax_ref) = '' OR jsonb_typeof(NEW.tax_classes) <> 'object') THEN
      RAISE EXCEPTION 'GST approval requires recorded authorized tax decision' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_gst_profile_immutable BEFORE UPDATE OR DELETE ON cow_commercialgstprofile
FOR EACH ROW EXECUTE FUNCTION cow_cm_gst_profile_guard();

-- Locks and interval checks ensure no conflicting active definition per target.
CREATE FUNCTION cow_cm_tariff_target_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t text; u uuid;
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Tariff assignment cannot be deleted; close/revise' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' THEN
     IF (OLD.policy_id,OLD.target_type,OLD.zone_id,OLD.group_id,OLD.channel,OLD.slot,OLD.starts_at,OLD.created_by_id)
        IS DISTINCT FROM (NEW.policy_id,NEW.target_type,NEW.zone_id,NEW.group_id,NEW.channel,NEW.slot,NEW.starts_at,NEW.created_by_id)
        OR (OLD.ends_at IS NOT NULL AND OLD.ends_at IS DISTINCT FROM NEW.ends_at) THEN
       RAISE EXCEPTION 'Tariff assignment immutable other than one-time closure' USING ERRCODE='23514';
     END IF;
  END IF;
  IF NOT EXISTS (
     SELECT 1 FROM cow_pricingpolicy p
     JOIN cow_commercialtariffpayload payload ON payload.policy_id=p.id
     WHERE p.id=NEW.policy_id AND p.status='PUBLISHED' AND p.scope->>'kind'='LIBRARY_COMPOSITE'
       AND payload.channel=NEW.channel) THEN
     RAISE EXCEPTION 'Assignment requires matching published library tariff' USING ERRCODE='23514';
  END IF;
  t := COALESCE(NEW.zone_id::text,NEW.group_id::text);
  PERFORM pg_advisory_xact_lock(hashtextextended('cow-cm-tariff:'||NEW.target_type||':'||t||':'||NEW.channel||':'||NEW.slot,0));
  IF EXISTS (
     SELECT 1 FROM cow_commercialtariffassignment a
     WHERE a.id<>NEW.id AND a.target_type=NEW.target_type AND a.zone_id IS NOT DISTINCT FROM NEW.zone_id
       AND a.group_id IS NOT DISTINCT FROM NEW.group_id AND a.channel=NEW.channel AND a.slot=NEW.slot
       AND (a.ends_at IS NULL OR NEW.starts_at IS NULL OR a.ends_at>NEW.starts_at)
       AND (NEW.ends_at IS NULL OR a.starts_at IS NULL OR NEW.ends_at>a.starts_at)
  ) THEN
    RAISE EXCEPTION 'Overlapping tariff assignment windows' USING ERRCODE='23P01';
  END IF;
  IF NEW.slot='SCHEDULED' AND NOT EXISTS (
     SELECT 1 FROM cow_commercialtariffassignment a
     WHERE a.target_type=NEW.target_type AND a.zone_id IS NOT DISTINCT FROM NEW.zone_id
       AND a.group_id IS NOT DISTINCT FROM NEW.group_id AND a.channel=NEW.channel AND a.slot='DEFAULT'
       AND (a.starts_at IS NULL OR a.starts_at<=NEW.starts_at)
       AND (a.ends_at IS NULL OR a.ends_at>=NEW.ends_at)
  ) THEN
     RAISE EXCEPTION 'A scheduled tariff requires uninterrupted default coverage' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_tariff_target BEFORE INSERT OR UPDATE OR DELETE ON cow_commercialtariffassignment
FOR EACH ROW EXECUTE FUNCTION cow_cm_tariff_target_guard();

CREATE FUNCTION cow_cm_gst_target_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'GST assignment cannot be deleted; close/revise' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' THEN
    IF (OLD.profile_id,OLD.zone_id,OLD.channel,OLD.starts_at,OLD.created_by_id)
       IS DISTINCT FROM (NEW.profile_id,NEW.zone_id,NEW.channel,NEW.starts_at,NEW.created_by_id)
       OR (OLD.ends_at IS NOT NULL AND OLD.ends_at IS DISTINCT FROM NEW.ends_at) THEN
       RAISE EXCEPTION 'GST assignment immutable other than one-time closure' USING ERRCODE='23514';
    END IF;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM cow_commercialgstprofile p WHERE p.id=NEW.profile_id AND p.status='APPROVED') THEN
     RAISE EXCEPTION 'Assignment requires approved GST profile' USING ERRCODE='23514';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('cow-cm-gst:'||NEW.zone_id::text||':'||NEW.channel,0));
  IF EXISTS (SELECT 1 FROM cow_commercialgstassignment a WHERE a.id<>NEW.id
     AND a.zone_id=NEW.zone_id AND a.channel=NEW.channel
     AND (a.ends_at IS NULL OR NEW.starts_at IS NULL OR a.ends_at>NEW.starts_at)
     AND (NEW.ends_at IS NULL OR a.starts_at IS NULL OR NEW.ends_at>a.starts_at)) THEN
    RAISE EXCEPTION 'Overlapping GST assignment windows' USING ERRCODE='23P01';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_gst_target BEFORE INSERT OR UPDATE OR DELETE ON cow_commercialgstassignment
FOR EACH ROW EXECUTE FUNCTION cow_cm_gst_target_guard();

CREATE FUNCTION cow_cm_membership_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Group membership cannot be deleted; close/revise' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' AND (
    (OLD.customer_id,OLD.group_id,OLD.starts_at,OLD.created_by_id)
    IS DISTINCT FROM (NEW.customer_id,NEW.group_id,NEW.starts_at,NEW.created_by_id)
    OR OLD.ends_at IS NOT NULL) THEN
    RAISE EXCEPTION 'Membership history immutable' USING ERRCODE='23514';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('cow-cm-group:'||NEW.customer_id::text,0));
  IF EXISTS (SELECT 1 FROM cow_commercialgroupmembership a WHERE a.id<>NEW.id
       AND a.customer_id=NEW.customer_id
       AND (a.ends_at IS NULL OR a.ends_at>NEW.starts_at)
       AND (NEW.ends_at IS NULL OR NEW.ends_at>a.starts_at)) THEN
    RAISE EXCEPTION 'Overlapping customer pricing groups' USING ERRCODE='23P01';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_membership BEFORE INSERT OR UPDATE OR DELETE ON cow_commercialgroupmembership
FOR EACH ROW EXECUTE FUNCTION cow_cm_membership_guard();

-- Deferred readiness prevents a transaction from leaving an enabled channel
-- without an unbounded default or an unbounded approved GST decision.
CREATE FUNCTION cow_cm_assert_zone_ready() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE zid uuid; ch text; v_check_at timestamptz := statement_timestamp();
BEGIN
  IF TG_TABLE_NAME='cow_commercialzonechannel' THEN
    zid:=COALESCE(NEW.zone_id,OLD.zone_id); ch:=COALESCE(NEW.channel,OLD.channel);
  ELSIF TG_TABLE_NAME='cow_commercialtariffassignment' THEN
    zid:=COALESCE(NEW.zone_id,OLD.zone_id); ch:=COALESCE(NEW.channel,OLD.channel);
  ELSIF TG_TABLE_NAME='cow_commercialgstassignment' THEN
    zid:=COALESCE(NEW.zone_id,OLD.zone_id); ch:=COALESCE(NEW.channel,OLD.channel);
  ELSE
    RETURN NULL;
  END IF;
  IF zid IS NULL OR NOT EXISTS(SELECT 1 FROM cow_commercialzonechannel c WHERE c.zone_id=zid AND c.channel=ch AND c.enabled) THEN
    RETURN NULL;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('cow-cm-ready:'||zid::text||':'||ch,0));
  IF NOT EXISTS(SELECT 1 FROM cow_servicezone z WHERE z.id=zid AND z.status='PUBLISHED'
      AND (z.starts_at IS NULL OR z.starts_at<=v_check_at) AND (z.ends_at IS NULL OR z.ends_at>v_check_at)) THEN
    RAISE EXCEPTION 'Enabled Zone channel requires effective published Zone' USING ERRCODE='23514';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM cow_commercialtariffassignment a JOIN cow_pricingpolicy p ON p.id=a.policy_id
      WHERE a.zone_id=zid AND a.channel=ch AND a.slot='DEFAULT' AND p.status='PUBLISHED'
        AND (a.starts_at IS NULL OR a.starts_at<=v_check_at) AND a.ends_at IS NULL) THEN
    RAISE EXCEPTION 'Enabled Zone channel requires unbounded default tariff' USING ERRCODE='23514';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM cow_commercialgstassignment a JOIN cow_commercialgstprofile p ON p.id=a.profile_id
      WHERE a.zone_id=zid AND a.channel=ch AND p.status='APPROVED'
        AND (a.starts_at IS NULL OR a.starts_at<=v_check_at) AND a.ends_at IS NULL) THEN
    RAISE EXCEPTION 'Enabled Zone channel requires unbounded approved GST' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER cow_cm_zone_readiness AFTER INSERT OR UPDATE OR DELETE ON cow_commercialzonechannel
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cow_cm_assert_zone_ready();
CREATE CONSTRAINT TRIGGER cow_cm_tariff_readiness AFTER INSERT OR UPDATE OR DELETE ON cow_commercialtariffassignment
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cow_cm_assert_zone_ready();
CREATE CONSTRAINT TRIGGER cow_cm_gst_readiness AFTER INSERT OR UPDATE OR DELETE ON cow_commercialgstassignment
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cow_cm_assert_zone_ready();

-- Never retire a published Zone while any new commercial channel is enabled.
CREATE FUNCTION cow_cm_zone_status_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status='PUBLISHED' AND NEW.status<>'PUBLISHED' AND EXISTS (
    SELECT 1 FROM cow_commercialzonechannel c WHERE c.zone_id=OLD.id AND c.enabled
  ) THEN
    RAISE EXCEPTION 'Pause commercial channels before retiring Zone' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_zone_status BEFORE UPDATE OF status ON cow_servicezone
FOR EACH ROW EXECUTE FUNCTION cow_cm_zone_status_guard();

-- Enforced lifecycle order for policy/GST retirement and group suspension.
CREATE FUNCTION cow_cm_policy_withdraw_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status='PUBLISHED' AND NEW.status<>'PUBLISHED'
    AND EXISTS(SELECT 1 FROM cow_commercialtariffassignment a
               WHERE a.policy_id=OLD.id AND (a.ends_at IS NULL OR a.ends_at>statement_timestamp())) THEN
    RAISE EXCEPTION 'Close future and active target assignments before tariff withdrawal' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_policy_withdraw BEFORE UPDATE OF status ON cow_pricingpolicy
FOR EACH ROW EXECUTE FUNCTION cow_cm_policy_withdraw_guard();

CREATE FUNCTION cow_cm_gst_retirement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status='APPROVED' AND NEW.status<>'APPROVED'
    AND EXISTS(SELECT 1 FROM cow_commercialgstassignment a
               WHERE a.profile_id=OLD.id AND (a.ends_at IS NULL OR a.ends_at>statement_timestamp())) THEN
    RAISE EXCEPTION 'Close future and active GST assignments before GST retirement' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_gst_retirement BEFORE UPDATE OF status ON cow_commercialgstprofile
FOR EACH ROW EXECUTE FUNCTION cow_cm_gst_retirement_guard();

CREATE FUNCTION cow_cm_group_retirement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.is_active AND NOT NEW.is_active AND (
     EXISTS(SELECT 1 FROM cow_commercialgroupmembership m
            WHERE m.group_id=OLD.id AND (m.ends_at IS NULL OR m.ends_at>statement_timestamp()))
     OR EXISTS(SELECT 1 FROM cow_commercialtariffassignment a WHERE a.group_id=OLD.id
            AND (a.ends_at IS NULL OR a.ends_at>statement_timestamp()))
  ) THEN
    RAISE EXCEPTION 'Close pricing group memberships and assignments before retirement' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_cm_group_retirement BEFORE UPDATE OF is_active ON cow_commercialpricinggroup
FOR EACH ROW EXECUTE FUNCTION cow_cm_group_retirement_guard();
"""
REVERSE_GUARDS = r"""
DROP INDEX IF EXISTS cow_cm_group_name_normalized;
DROP TRIGGER IF EXISTS cow_cm_group_retirement ON cow_commercialpricinggroup;
DROP FUNCTION IF EXISTS cow_cm_group_retirement_guard();
DROP TRIGGER IF EXISTS cow_cm_gst_retirement ON cow_commercialgstprofile;
DROP FUNCTION IF EXISTS cow_cm_gst_retirement_guard();
DROP TRIGGER IF EXISTS cow_cm_policy_withdraw ON cow_pricingpolicy;
DROP FUNCTION IF EXISTS cow_cm_policy_withdraw_guard();
DROP TRIGGER IF EXISTS cow_cm_zone_status ON cow_servicezone;
DROP FUNCTION IF EXISTS cow_cm_zone_status_guard();
DROP TRIGGER IF EXISTS cow_cm_tariff_readiness ON cow_commercialtariffassignment;
DROP TRIGGER IF EXISTS cow_cm_gst_readiness ON cow_commercialgstassignment;
DROP TRIGGER IF EXISTS cow_cm_zone_readiness ON cow_commercialzonechannel;
DROP FUNCTION IF EXISTS cow_cm_assert_zone_ready();
DROP TRIGGER IF EXISTS cow_cm_membership ON cow_commercialgroupmembership;
DROP FUNCTION IF EXISTS cow_cm_membership_guard();
DROP TRIGGER IF EXISTS cow_cm_gst_target ON cow_commercialgstassignment;
DROP FUNCTION IF EXISTS cow_cm_gst_target_guard();
DROP TRIGGER IF EXISTS cow_cm_tariff_target ON cow_commercialtariffassignment;
DROP FUNCTION IF EXISTS cow_cm_tariff_target_guard();
DROP TRIGGER IF EXISTS cow_cm_gst_profile_immutable ON cow_commercialgstprofile;
DROP FUNCTION IF EXISTS cow_cm_gst_profile_guard();
DROP TRIGGER IF EXISTS cow_cm_payload_immutable ON cow_commercialtariffpayload;
DROP FUNCTION IF EXISTS cow_cm_payload_guard();
"""


class Migration(migrations.Migration):
    dependencies=[('cow','0009_zone_admin_operations')]
    operations=[
        migrations.CreateModel(name='CommercialPricingGroup',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,serialize=False,editable=False)),
            ('name',models.CharField(max_length=100,unique=True)),
            ('description',models.CharField(max_length=400,blank=True)),
            ('is_active',models.BooleanField(default=True)),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('created_by',models.ForeignKey(to='cow.principal',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_groups_created')),
        ]),
        migrations.CreateModel(name='CommercialGroupMembership',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,serialize=False,editable=False)),
            ('starts_at',models.DateTimeField()),
            ('ends_at',models.DateTimeField(null=True,blank=True)),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('customer',models.ForeignKey(to='cow.customerprofile',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_memberships')),
            ('group',models.ForeignKey(to='cow.commercialpricinggroup',on_delete=django.db.models.deletion.PROTECT,related_name='customer_memberships')),
            ('created_by',models.ForeignKey(to='cow.principal',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_memberships_created')),
        ],options={'constraints':[models.CheckConstraint(condition=Q(ends_at__isnull=True)|Q(ends_at__gt=F('starts_at')),name='cow_cm_group_window')]}),
        migrations.CreateModel(name='CommercialTariffPayload',fields=[
            ('policy',models.OneToOneField(primary_key=True,serialize=False,to='cow.pricingpolicy',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_payload')),
            ('channel',models.CharField(max_length=12,choices=[(x,x) for x in ('ROAMING','ON_SITE_QR')])),
            ('plan',models.JSONField()),('created_at',models.DateTimeField(auto_now_add=True)),
            ('created_by',models.ForeignKey(to='cow.principal',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_tariffs_authored')),
        ]),
        migrations.CreateModel(name='CommercialGSTProfile',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,serialize=False,editable=False)),
            ('profile_key',models.CharField(max_length=64)),('profile_version',models.PositiveIntegerField()),
            ('status',models.CharField(max_length=10,choices=[(x,x) for x in ('DRAFT','APPROVED','RETIRED','REVOKED')],default='DRAFT')),
            ('seller_tax_ref',models.CharField(max_length=200)),('legal_decision_ref',models.CharField(max_length=200)),
            ('tax_classes',models.JSONField()),('created_at',models.DateTimeField(auto_now_add=True)),
            ('created_by',models.ForeignKey(to='cow.principal',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_gst_profiles_created')),
            ('approved_by',models.ForeignKey(to='cow.principal',on_delete=django.db.models.deletion.PROTECT,null=True,blank=True,related_name='commercial_gst_profiles_approved')),
        ],options={'constraints':[
            models.UniqueConstraint(fields=['profile_key','profile_version'],name='cow_cm_gst_key_revision'),
            models.CheckConstraint(condition=Q(profile_version__gt=0),name='cow_cm_gst_version_positive'),
        ]}),
        migrations.CreateModel(name='CommercialTariffAssignment',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,serialize=False,editable=False)),
            ('target_type',models.CharField(max_length=5,choices=[(x,x) for x in ('ZONE','GROUP')])),
            ('channel',models.CharField(max_length=12,choices=[(x,x) for x in ('ROAMING','ON_SITE_QR')])),
            ('slot',models.CharField(max_length=9,choices=[(x,x) for x in ('DEFAULT','SCHEDULED')])),
            ('starts_at',models.DateTimeField(null=True,blank=True)),('ends_at',models.DateTimeField(null=True,blank=True)),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('zone',models.ForeignKey(to='cow.servicezone',null=True,blank=True,on_delete=django.db.models.deletion.PROTECT,related_name='commercial_tariff_assignments')),
            ('group',models.ForeignKey(to='cow.commercialpricinggroup',null=True,blank=True,on_delete=django.db.models.deletion.PROTECT,related_name='tariff_assignments')),
            ('policy',models.ForeignKey(to='cow.pricingpolicy',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_target_assignments')),
            ('created_by',models.ForeignKey(to='cow.principal',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_tariff_assignments_created')),
        ],options={'constraints':[
            models.CheckConstraint(condition=Q(target_type='ZONE',zone__isnull=False,group__isnull=True)|Q(target_type='GROUP',zone__isnull=True,group__isnull=False),name='cow_cm_tariff_one_target'),
            models.CheckConstraint(condition=Q(ends_at__isnull=True)|Q(starts_at__isnull=True)|Q(ends_at__gt=F('starts_at')),name='cow_cm_tariff_assignment_window'),
            models.CheckConstraint(condition=Q(slot='DEFAULT')|Q(slot='SCHEDULED',starts_at__isnull=False,ends_at__isnull=False),name='cow_cm_tariff_schedule_bounded'),
        ]}),
        migrations.CreateModel(name='CommercialGSTAssignment',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,serialize=False,editable=False)),
            ('channel',models.CharField(max_length=12,choices=[(x,x) for x in ('ROAMING','ON_SITE_QR')])),
            ('starts_at',models.DateTimeField(null=True,blank=True)),('ends_at',models.DateTimeField(null=True,blank=True)),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('zone',models.ForeignKey(to='cow.servicezone',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_gst_assignments')),
            ('profile',models.ForeignKey(to='cow.commercialgstprofile',on_delete=django.db.models.deletion.PROTECT,related_name='zone_assignments')),
            ('created_by',models.ForeignKey(to='cow.principal',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_gst_assignments_created')),
        ],options={'constraints':[models.CheckConstraint(condition=Q(ends_at__isnull=True)|Q(starts_at__isnull=True)|Q(ends_at__gt=F('starts_at')),name='cow_cm_gst_assignment_window')]}),
        migrations.CreateModel(name='CommercialZoneChannel',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,serialize=False,editable=False)),
            ('channel',models.CharField(max_length=12,choices=[(x,x) for x in ('ROAMING','ON_SITE_QR')])),
            ('enabled',models.BooleanField(default=False)),('updated_at',models.DateTimeField(auto_now=True)),
            ('zone',models.ForeignKey(to='cow.servicezone',on_delete=django.db.models.deletion.PROTECT,related_name='commercial_channels')),
        ],options={'constraints':[models.UniqueConstraint(fields=['zone','channel'],name='cow_cm_zone_channel_unique')]}),
        migrations.RunSQL(OTHER_GUARDS,reverse_sql=REVERSE_GUARDS),
        migrations.RunSQL(FORWARD_GUARD,reverse_sql=BASE),
    ]
