"""Reviewed PostgreSQL-only invariants within the ONE Django migration ledger."""
from django.db import migrations

FORWARD = r"""
CREATE FUNCTION cow_guard_principal_plane() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual text;
BEGIN
    IF TG_ARGV[0] = 'owner' THEN
        SELECT plane INTO actual FROM cow_principal WHERE id = NEW.owner_id;
    ELSIF TG_ARGV[0] = 'customer' THEN
        SELECT plane INTO actual FROM cow_principal WHERE id = NEW.customer_id;
    ELSE
        SELECT plane INTO actual FROM cow_principal WHERE id = NEW.principal_id;
    END IF;
    IF actual IS DISTINCT FROM TG_ARGV[1] THEN
        RAISE EXCEPTION 'Invalid principal plane' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cow_app_vehicle_plane BEFORE INSERT OR UPDATE OF owner_id ON cow_customervehicle
    FOR EACH ROW EXECUTE FUNCTION cow_guard_principal_plane('owner','app');
CREATE TRIGGER cow_app_address_plane BEFORE INSERT OR UPDATE OF owner_id ON cow_customeraddress
    FOR EACH ROW EXECUTE FUNCTION cow_guard_principal_plane('owner','app');
CREATE TRIGGER cow_app_request_plane BEFORE INSERT OR UPDATE OF customer_id ON cow_servicerequest
    FOR EACH ROW EXECUTE FUNCTION cow_guard_principal_plane('customer','app');
CREATE TRIGGER cow_driver_profile_plane BEFORE INSERT OR UPDATE OF principal_id ON cow_driverprofile
    FOR EACH ROW EXECUTE FUNCTION cow_guard_principal_plane('principal','driver');
CREATE TRIGGER cow_admin_role_plane BEFORE INSERT OR UPDATE OF principal_id ON cow_adminrole
    FOR EACH ROW EXECUTE FUNCTION cow_guard_principal_plane('principal','admin');
CREATE TRIGGER cow_admin_override_plane BEFORE INSERT OR UPDATE OF principal_id ON cow_adminoverride
    FOR EACH ROW EXECUTE FUNCTION cow_guard_principal_plane('principal','admin');

CREATE FUNCTION cow_guard_job_quote() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE quoted_request uuid;
BEGIN
    SELECT request_id INTO quoted_request FROM cow_quote WHERE id = NEW.accepted_quote_id;
    IF quoted_request IS DISTINCT FROM NEW.request_id THEN
        RAISE EXCEPTION 'Job and quote must share request' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cow_job_quote_consistency BEFORE INSERT OR UPDATE OF request_id,accepted_quote_id ON cow_servicejob
    FOR EACH ROW EXECUTE FUNCTION cow_guard_job_quote();

CREATE FUNCTION cow_protect_quote_truth() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD.request_id, OLD.pricing_policy_id, OLD.pricing_snapshot, OLD.total_paise, OLD.currency, OLD.expires_at)
     IS DISTINCT FROM
     (NEW.request_id, NEW.pricing_policy_id, NEW.pricing_snapshot, NEW.total_paise, NEW.currency, NEW.expires_at) THEN
    RAISE EXCEPTION 'Quotation terms are immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_quote_immutable BEFORE UPDATE ON cow_quote
    FOR EACH ROW EXECUTE FUNCTION cow_protect_quote_truth();

CREATE FUNCTION cow_protect_request_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD.customer_id,OLD.vehicle_snapshot,OLD.address_snapshot,OLD.requested_energy_wh)
     IS DISTINCT FROM
     (NEW.customer_id,NEW.vehicle_snapshot,NEW.address_snapshot,NEW.requested_energy_wh) THEN
    RAISE EXCEPTION 'Request ownership and snapshots are immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_request_snapshot_immutable BEFORE UPDATE ON cow_servicerequest
    FOR EACH ROW EXECUTE FUNCTION cow_protect_request_snapshot();

CREATE FUNCTION cow_protect_published_pricing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('ACTIVE','RETIRED') AND
    (OLD.name,OLD.revision,OLD.currency,OLD.base_service_paise,OLD.per_kwh_paise,OLD.effective_from)
       IS DISTINCT FROM
    (NEW.name,NEW.revision,NEW.currency,NEW.base_service_paise,NEW.per_kwh_paise,NEW.effective_from) THEN
    RAISE EXCEPTION 'Published pricing terms cannot change' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cow_pricing_terms_immutable BEFORE UPDATE ON cow_pricingpolicy
    FOR EACH ROW EXECUTE FUNCTION cow_protect_published_pricing();
"""
REVERSE = r"""
DROP TRIGGER IF EXISTS cow_job_quote_consistency ON cow_servicejob;
DROP FUNCTION IF EXISTS cow_guard_job_quote();
DROP TRIGGER IF EXISTS cow_pricing_terms_immutable ON cow_pricingpolicy;
DROP FUNCTION IF EXISTS cow_protect_published_pricing();
DROP TRIGGER IF EXISTS cow_request_snapshot_immutable ON cow_servicerequest;
DROP FUNCTION IF EXISTS cow_protect_request_snapshot();
DROP TRIGGER IF EXISTS cow_quote_immutable ON cow_quote;
DROP FUNCTION IF EXISTS cow_protect_quote_truth();
DROP TRIGGER IF EXISTS cow_admin_override_plane ON cow_adminoverride;
DROP TRIGGER IF EXISTS cow_admin_role_plane ON cow_adminrole;
DROP TRIGGER IF EXISTS cow_driver_profile_plane ON cow_driverprofile;
DROP TRIGGER IF EXISTS cow_app_request_plane ON cow_servicerequest;
DROP TRIGGER IF EXISTS cow_app_address_plane ON cow_customeraddress;
DROP TRIGGER IF EXISTS cow_app_vehicle_plane ON cow_customervehicle;
DROP FUNCTION IF EXISTS cow_guard_principal_plane();
"""

class Migration(migrations.Migration):
    dependencies = [("cow", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
