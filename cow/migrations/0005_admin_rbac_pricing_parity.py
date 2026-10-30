"""Additive Admin pricing revisions, preserving every legacy policy and quote."""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F, Q

FORWARD = r"""
CREATE FUNCTION cow_admin_pricing_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND (OLD.policy_key IS NULL) IS DISTINCT FROM
        (NEW.policy_key IS NULL) THEN
        RAISE EXCEPTION 'Legacy and Admin pricing revisions cannot be converted'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.policy_key IS NOT NULL THEN
        IF (OLD.name, OLD.revision, OLD.currency, OLD.base_service_paise,
            OLD.per_kwh_paise, OLD.policy_key, OLD.policy_version,
            OLD.scope, OLD.rules, OLD.starts_at, OLD.ends_at, OLD.created_by_id)
           IS DISTINCT FROM
           (NEW.name, NEW.revision, NEW.currency, NEW.base_service_paise,
            NEW.per_kwh_paise, NEW.policy_key, NEW.policy_version,
            NEW.scope, NEW.rules, NEW.starts_at, NEW.ends_at, NEW.created_by_id) THEN
            RAISE EXCEPTION 'Admin pricing revision terms are immutable' USING ERRCODE = '23514';
        END IF;
        IF OLD.status = NEW.status THEN
            IF OLD.state_version IS DISTINCT FROM NEW.state_version THEN
                RAISE EXCEPTION 'Admin pricing state version changed without state transition'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NOT ((OLD.status = 'DRAFT' AND NEW.status = 'PUBLISHED')
                OR (OLD.status = 'PUBLISHED' AND NEW.status = 'WITHDRAWN'))
            OR NEW.state_version IS DISTINCT FROM OLD.state_version + 1 THEN
            RAISE EXCEPTION 'Invalid Admin pricing lifecycle transition'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.policy_key IS NOT NULL THEN
        IF NEW.policy_version IS NULL OR NEW.policy_version = 0 OR NEW.currency <> 'INR'
            OR NEW.name IS DISTINCT FROM NEW.policy_key
            OR NEW.revision IS DISTINCT FROM NEW.policy_version
            OR NEW.policy_key !~ '^[A-Z][A-Z0-9_]{0,63}$'
            OR NEW.created_by_id IS NULL
            OR (SELECT plane FROM cow_principal WHERE id = NEW.created_by_id) IS DISTINCT FROM 'admin'
            OR jsonb_typeof(NEW.scope) IS DISTINCT FROM 'object'
            OR NOT (NEW.scope ? 'kind' AND NEW.scope ? 'key')
            OR jsonb_typeof(NEW.rules) IS DISTINCT FROM 'array'
            OR jsonb_array_length(NEW.rules) = 0
            OR NEW.state_version < 1
            OR (NEW.ends_at IS NOT NULL AND
                (NEW.starts_at IS NULL OR NEW.ends_at <= NEW.starts_at)) THEN
            RAISE EXCEPTION 'Invalid Admin pricing revision shape' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' AND (NEW.status <> 'DRAFT' OR NEW.state_version <> 1) THEN
            RAISE EXCEPTION 'Admin pricing starts as DRAFT version 1' USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'PUBLISHED' THEN
            -- Same exact lock held by all DB writers, including direct SQL writes.
            PERFORM pg_advisory_xact_lock(hashtextextended('cow-pricing-scope-db:' || NEW.scope::text, 0));
            IF EXISTS (
                SELECT 1 FROM cow_pricingpolicy p
                 WHERE p.id <> NEW.id AND p.status = 'PUBLISHED' AND p.scope = NEW.scope
                   AND (p.ends_at IS NULL OR NEW.starts_at IS NULL OR NEW.starts_at < p.ends_at)
                   AND (NEW.ends_at IS NULL OR p.starts_at IS NULL OR p.starts_at < NEW.ends_at)
            ) THEN
                RAISE EXCEPTION 'Published Admin pricing windows overlap' USING ERRCODE = '23514';
            END IF;
        END IF;
    ELSIF TG_OP = 'UPDATE' AND OLD.policy_key IS NOT NULL THEN
        RAISE EXCEPTION 'Cannot convert Admin pricing revision to legacy' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cow_admin_pricing_revision_guard
  BEFORE INSERT OR UPDATE ON cow_pricingpolicy
  FOR EACH ROW EXECUTE FUNCTION cow_admin_pricing_guard();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS cow_admin_pricing_revision_guard ON cow_pricingpolicy;
DROP FUNCTION IF EXISTS cow_admin_pricing_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("cow", "0004_admin_charger_connector_pairing_parity")]
    operations = [
        migrations.AddField(
            model_name="pricingpolicy",
            name="policy_key",
            field=models.CharField(max_length=64, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="pricingpolicy",
            name="policy_version",
            field=models.PositiveBigIntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="pricingpolicy", name="scope", field=models.JSONField(default=dict)
        ),
        migrations.AddField(
            model_name="pricingpolicy", name="rules", field=models.JSONField(default=list)
        ),
        migrations.AddField(
            model_name="pricingpolicy",
            name="state_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="pricingpolicy",
            name="starts_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="pricingpolicy",
            name="ends_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="pricingpolicy",
            name="created_by",
            field=models.ForeignKey(
                to="cow.principal",
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="authored_pricing_revisions",
            ),
        ),
        migrations.AddConstraint(
            "pricingpolicy",
            models.UniqueConstraint(
                fields=("policy_key", "policy_version"),
                condition=Q(policy_key__isnull=False),
                name="cow_admin_pricing_revision_unique",
            ),
        ),
        migrations.AddConstraint(
            "pricingpolicy",
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True)
                | (Q(starts_at__isnull=False) & Q(ends_at__gt=F("starts_at"))),
                name="cow_admin_pricing_window",
            ),
        ),
        migrations.RunSQL(FORWARD, reverse_sql=REVERSE),
    ]
