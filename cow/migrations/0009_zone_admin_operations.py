"""Sprint 7: real Zone composite tariff publication on the existing policy ledger.

The ordinary Admin pricing rules remain unchanged. Only the two reserved Zone
scope kinds require an immutable ZoneTariffPayload instead of generic rules.
SQL guard changes are reversible; migration 0007 is never edited or bypassed.
"""
from importlib import import_module

from django.db import migrations, models
from django.db.models import Q

# 0007 is an immutable migration containing the canonical policy trigger body.
# Reuse that exact body instead of manually copying a possibly divergent guard.
BASE_GUARD = import_module('cow.migrations.0007_rbac_pricing_consequential_parity').FORWARD
_OLD = 'OR jsonb_array_length(NEW.rules) = 0'
_NEW = "OR (jsonb_array_length(NEW.rules) = 0 AND NEW.scope->>'kind' NOT IN ('ZONE_ROAMING', 'ZONE_ON_SITE_QR'))"
_POINT = '    RETURN NEW;\nEND $$;'
assert BASE_GUARD.count(_OLD) == 1 and BASE_GUARD.count(_POINT) == 1, '0007 policy guard changed: review required'

_ZONE_GUARD = r"""
    IF NEW.policy_key IS NOT NULL AND NEW.scope ->> 'kind' IN ('ZONE_ROAMING', 'ZONE_ON_SITE_QR') THEN
        IF NEW.rules IS DISTINCT FROM '[]'::jsonb THEN
            RAISE EXCEPTION 'Composite Zone tariff cannot masquerade as generic pricing rules'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'PUBLISHED' AND NOT EXISTS (
            SELECT 1 FROM cow_zonetariffpayload zp
            JOIN cow_servicezone z ON z.id = zp.zone_id
            WHERE zp.policy_id = NEW.id
              AND z.status = 'PUBLISHED'
              AND zp.channel = substring(NEW.scope ->> 'kind' FROM 6)
              AND z.zone_key = NEW.scope ->> 'key'
              AND zp.plan ->> 'policy_id' = NEW.policy_key
              AND zp.plan ->> 'policy_version' = NEW.policy_version::text
              AND zp.plan ->> 'zone_id' = z.zone_key
              AND zp.plan ->> 'channel' = zp.channel
              AND (z.starts_at IS NULL OR z.starts_at <= coalesce(NEW.starts_at, statement_timestamp()))
              AND (z.ends_at IS NULL OR z.ends_at > coalesce(NEW.starts_at, statement_timestamp()))
              AND (z.ends_at IS NULL OR (NEW.ends_at IS NOT NULL AND NEW.ends_at <= z.ends_at))
              AND EXISTS (
                  SELECT 1 FROM cow_zonegstassignment gst
                  WHERE gst.zone_id = z.id AND gst.channel = zp.channel
                    AND gst.status = 'APPROVED'
                    AND gst.starts_at <= coalesce(NEW.starts_at, statement_timestamp())
                    AND (gst.ends_at IS NULL OR gst.ends_at > coalesce(NEW.starts_at, statement_timestamp()))
              )
        ) THEN
            RAISE EXCEPTION 'Published Zone tariff requires matching published effective Zone and payload'
                USING ERRCODE = '23514';
        END IF;
    END IF;
"""

FORWARD = BASE_GUARD.replace(_OLD, _NEW).replace(_POINT, _ZONE_GUARD + _POINT)
REVERSE = BASE_GUARD


class Migration(migrations.Migration):
    dependencies = [('cow', '0008_zone_commercial_registry')]
    operations = [
        migrations.AddConstraint(
            model_name='servicezone',
            constraint=models.UniqueConstraint(fields=['zone_key'], condition=Q(status='PUBLISHED'),
                                             name='cow_zone_single_published_key'),
        ),
        migrations.RunSQL(FORWARD, reverse_sql=REVERSE),
    ]
