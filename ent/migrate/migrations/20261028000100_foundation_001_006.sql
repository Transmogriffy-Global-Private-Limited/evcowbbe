-- Immutable SQL foundation snapshot: PostgreSQL 001 through proposed 006.
-- New databases replay this baseline; existing databases must use Atlas --baseline
-- ONLY after 006 has been applied and independently verified. Do not edit.

-- Beginning immutable migration 001_auth.sql (38b146ed4adb9ef6162843dc2adaede7896d8616cc39395d8b69a09eb85b635c)
-- Auth-only foundation. Future app roles and RBAC capabilities are separate, gated work.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version integer PRIMARY KEY,
  checksum text NOT NULL CHECK (length(checksum)=64),
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
 id uuid PRIMARY KEY,
 email text COLLATE "C" NOT NULL UNIQUE,
 password_hash text NOT NULL,
 email_verified_at timestamptz,
 two_factor_enabled boolean NOT NULL DEFAULT false,
 disabled_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK (email = lower(email)),
 CHECK (length(email) BETWEEN 3 AND 254)
);
CREATE TABLE principals (
 account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 plane text NOT NULL CHECK (plane IN ('app','driver','admin')),
 status text NOT NULL CHECK (status IN ('ACTIVE','PENDING','SUSPENDED')),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (account_id, plane)
);
CREATE TABLE auth_sessions (
 id uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 plane text NOT NULL CHECK (plane IN ('app','driver','admin')),
 access_digest bytea NOT NULL UNIQUE,
 refresh_digest bytea NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 access_expires_at timestamptz NOT NULL,
 refresh_expires_at timestamptz NOT NULL,
 absolute_expires_at timestamptz NOT NULL,
 refresh_generation integer NOT NULL DEFAULT 0,
 last_seen_at timestamptz,
 revoked_at timestamptz,
 revoke_reason text,
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane)
);
CREATE INDEX sessions_account ON auth_sessions(account_id) WHERE revoked_at IS NULL;
CREATE INDEX sessions_expiry ON auth_sessions(absolute_expires_at);
CREATE TABLE consumed_refresh_tokens (
 digest bytea PRIMARY KEY,
 session_id uuid NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
 consumed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX consumed_refresh_expiry ON consumed_refresh_tokens(consumed_at);

CREATE TABLE auth_challenges (
 id uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 plane text NOT NULL CHECK (plane IN ('app','driver','admin')),
 purpose text NOT NULL CHECK (purpose IN ('LOGIN_2FA','ENABLE_2FA','DISABLE_2FA')),
 digest bytea NOT NULL,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 expires_at timestamptz NOT NULL,
 last_sent_at timestamptz NOT NULL DEFAULT now(),
 consumed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX challenges_account ON auth_challenges(account_id,purpose,created_at DESC);

CREATE TABLE auth_action_tokens (
 digest bytea PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 kind text NOT NULL CHECK (kind IN ('VERIFY_EMAIL','RESET_PASSWORD')),
 expires_at timestamptz NOT NULL,
 consumed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX action_account_kind ON auth_action_tokens(account_id,kind);

CREATE TABLE auth_rate_limits (
 bucket text NOT NULL,
 key_digest bytea NOT NULL,
 window_start timestamptz NOT NULL,
 hits integer NOT NULL CHECK(hits>=0),
 PRIMARY KEY(bucket,key_digest)
);

-- All timestamps UTC at API boundary; PostgreSQL timestamptz stores instants.
CREATE TABLE security_audit (
 id bigserial PRIMARY KEY,
 account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 event text NOT NULL,
 plane text,
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX security_audit_account ON security_audit(account_id,occurred_at DESC);
INSERT INTO schema_migrations(version,checksum) VALUES (1,'38b146ed4adb9ef6162843dc2adaede7896d8616cc39395d8b69a09eb85b635c');
-- Completed immutable migration 001_auth.sql

-- Beginning immutable migration 002_outbox_admin_rbac.sql (3d4fa76de2cd0c8326b9c659c50876ceccab2fb5bbe2542e6a318c75b422adbf)
-- Additive auth-email outbox and scoped Admin RBAC. Migration 001 is immutable.
CREATE TABLE auth_mail_outbox (
 id uuid PRIMARY KEY,
 account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 recipient_email text NOT NULL,
 template text NOT NULL CHECK (template IN ('verification','reset','otp','security')),
 payload_ciphertext bytea,
 payload_nonce bytea,
 credential_kind text CHECK (credential_kind IN ('VERIFY_EMAIL','RESET_PASSWORD','OTP')),
 credential_digest bytea,
 credential_challenge_id uuid,
 expires_at timestamptz,
 state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','LEASED','SENT','EXPIRED','DEAD')),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid,
 lease_until timestamptz,
 last_error_class text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 sent_at timestamptz,
 CHECK ((payload_ciphertext IS NULL) = (payload_nonce IS NULL)),
 CHECK ((credential_kind IS NULL AND credential_digest IS NULL AND credential_challenge_id IS NULL)
     OR (credential_kind IN ('VERIFY_EMAIL','RESET_PASSWORD') AND credential_digest IS NOT NULL AND credential_challenge_id IS NULL)
     OR (credential_kind='OTP' AND credential_digest IS NOT NULL AND credential_challenge_id IS NOT NULL))
);
CREATE INDEX auth_mail_outbox_due ON auth_mail_outbox(next_attempt_at, created_at)
 WHERE state IN ('PENDING','LEASED');
CREATE INDEX auth_mail_outbox_account ON auth_mail_outbox(account_id,created_at DESC);

CREATE TABLE admin_role_assignments (
 account_id uuid NOT NULL,
 plane text NOT NULL DEFAULT 'admin' CHECK (plane='admin'),
 role_key text NOT NULL CHECK (role_key IN ('FULL_ADMIN','OPERATIONS','DRIVER_RECRUITMENT','FINANCE_COMMERCIAL','SUPPORT','TECHNICAL_HAL','READ_ONLY')),
 assigned_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 assigned_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,role_key),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane)
);
CREATE TABLE admin_capability_overrides (
 account_id uuid NOT NULL,
 plane text NOT NULL DEFAULT 'admin' CHECK (plane='admin'),
 capability text NOT NULL,
 decision text NOT NULL CHECK (decision IN ('ALLOW','DENY')),
 changed_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 changed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,capability),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane)
);
CREATE TABLE admin_rbac_audit (
 id bigserial PRIMARY KEY,
 actor_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 target_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 operation text NOT NULL,
 subject text NOT NULL,
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX admin_rbac_audit_target ON admin_rbac_audit(target_id,occurred_at DESC);
INSERT INTO schema_migrations(version,checksum) VALUES (2,'3d4fa76de2cd0c8326b9c659c50876ceccab2fb5bbe2542e6a318c75b422adbf');
-- Completed immutable migration 002_outbox_admin_rbac.sql

-- Beginning immutable migration 003_fleet_inventory.sql (ea0947bd655fd442069541b8ff0e51b1276c60f64bdbe04b9750e94f798cfc41)
-- Fleet master data. No driver claims, HAL telemetry or inferred dispatch eligibility.
-- Migration 001 and 002 remain immutable. This file must be mirrored in /migrations.
CREATE TABLE service_vehicles (
 id uuid PRIMARY KEY,
 asset_tag text COLLATE "C" NOT NULL UNIQUE CHECK (length(asset_tag) BETWEEN 2 AND 64),
 registration_number text COLLATE "C" UNIQUE CHECK (registration_number IS NULL OR length(registration_number) BETWEEN 2 AND 32),
 make text NOT NULL CHECK (length(make) BETWEEN 1 AND 120),
 model text NOT NULL CHECK (length(model) BETWEEN 1 AND 120),
 variant text,
 vehicle_type text,
 ownership_type text,
 registration_valid_until date,
 insurance_valid_until date,
 inspection_status text NOT NULL DEFAULT 'UNKNOWN' CHECK (inspection_status IN ('UNKNOWN','VALID','FAILED','EXPIRED')),
 operational_status text NOT NULL DEFAULT 'AVAILABLE' CHECK (operational_status IN ('AVAILABLE','OUT_OF_SERVICE','RETIRED')),
 maintenance_status text NOT NULL DEFAULT 'UNKNOWN' CHECK (maintenance_status IN ('UNKNOWN','CLEAR','SCHEDULED','ACTIVE')),
 is_active boolean NOT NULL DEFAULT true,
 notes text CHECK (notes IS NULL OR length(notes) <= 4000),
 created_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 updated_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
 CHECK (operational_status <> 'RETIRED' OR is_active = false)
);
CREATE INDEX service_vehicles_search ON service_vehicles(operational_status,created_at,id);

CREATE TABLE chargers (
 id uuid PRIMARY KEY,
 asset_tag text COLLATE "C" NOT NULL UNIQUE CHECK (length(asset_tag) BETWEEN 2 AND 64),
 serial_number text COLLATE "C" UNIQUE CHECK (serial_number IS NULL OR length(serial_number) BETWEEN 2 AND 120),
 manufacturer text NOT NULL CHECK (length(manufacturer) BETWEEN 1 AND 120),
 model text NOT NULL CHECK (length(model) BETWEEN 1 AND 120),
 rated_power_w integer CHECK (rated_power_w IS NULL OR rated_power_w > 0),
 protocol_family text,
 protocol_version text,
 hal_identity text COLLATE "C" UNIQUE,
 operational_status text NOT NULL DEFAULT 'AVAILABLE' CHECK (operational_status IN ('AVAILABLE','OUT_OF_SERVICE','RETIRED')),
 maintenance_status text NOT NULL DEFAULT 'UNKNOWN' CHECK (maintenance_status IN ('UNKNOWN','CLEAR','SCHEDULED','ACTIVE')),
 is_active boolean NOT NULL DEFAULT true,
 notes text CHECK (notes IS NULL OR length(notes) <= 4000),
 created_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 updated_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
 CHECK (operational_status <> 'RETIRED' OR is_active = false)
);
CREATE INDEX chargers_search ON chargers(operational_status,created_at,id);

CREATE TABLE charger_connectors (
 id uuid PRIMARY KEY,
 charger_id uuid NOT NULL REFERENCES chargers(id) ON DELETE RESTRICT,
 connector_number integer NOT NULL CHECK (connector_number > 0),
 connector_type text NOT NULL CHECK (length(connector_type) BETWEEN 2 AND 64),
 max_power_w integer NOT NULL CHECK (max_power_w > 0),
 supports_ac boolean NOT NULL,
 supports_dc boolean NOT NULL,
 is_active boolean NOT NULL DEFAULT true,
 created_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 updated_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
 UNIQUE(charger_id,connector_number),
 CHECK (supports_ac OR supports_dc)
);
CREATE INDEX charger_connectors_charger ON charger_connectors(charger_id,connector_number);

CREATE TABLE vehicle_charger_pairings (
 id uuid PRIMARY KEY,
 vehicle_id uuid NOT NULL REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 charger_id uuid NOT NULL REFERENCES chargers(id) ON DELETE RESTRICT,
 effective_from timestamptz NOT NULL,
 effective_to timestamptz,
 status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ENDED','INVALIDATED')),
 source text NOT NULL DEFAULT 'ADMIN' CHECK (source IN ('ADMIN','RECONCILIATION')),
 created_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
 end_reason text CHECK (end_reason IS NULL OR length(btrim(end_reason)) BETWEEN 8 AND 500),
 ended_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 CHECK (effective_to IS NULL OR effective_to > effective_from),
 CHECK ((status = 'ACTIVE' AND effective_to IS NULL) OR (status <> 'ACTIVE' AND effective_to IS NOT NULL))
);
-- A charger can have only one exclusive current physical pairing.
CREATE UNIQUE INDEX vehicle_charger_one_active ON vehicle_charger_pairings(charger_id) WHERE status='ACTIVE';
CREATE INDEX vehicle_charger_history ON vehicle_charger_pairings(vehicle_id,effective_from DESC);

CREATE TABLE asset_maintenance_periods (
 id uuid PRIMARY KEY,
 vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 starts_at timestamptz NOT NULL,
 ends_at timestamptz NOT NULL,
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
 created_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((vehicle_id IS NOT NULL) <> (charger_id IS NOT NULL)),
 CHECK (ends_at > starts_at)
);
CREATE INDEX vehicle_maintenance_window ON asset_maintenance_periods(vehicle_id,starts_at,ends_at) WHERE vehicle_id IS NOT NULL;
CREATE INDEX charger_maintenance_window ON asset_maintenance_periods(charger_id,starts_at,ends_at) WHERE charger_id IS NOT NULL;

CREATE TABLE fleet_asset_audit (
 id bigserial PRIMARY KEY,
 actor_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 asset_kind text NOT NULL CHECK (asset_kind IN ('VEHICLE','CHARGER','CONNECTOR','PAIRING','MAINTENANCE')),
 asset_id uuid NOT NULL,
 action text NOT NULL,
 version bigint,
 reason text,
 before_value jsonb,
 after_value jsonb,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX fleet_asset_audit_resource ON fleet_asset_audit(asset_kind,asset_id,id DESC);

-- Reusable, transaction-bound mutation receipts. No secret payloads persisted.
CREATE TABLE mutation_idempotency_receipts (
 actor_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 request_key text COLLATE "C" NOT NULL CHECK (length(request_key) BETWEEN 16 AND 128),
 request_digest bytea NOT NULL CHECK (octet_length(request_digest)=32),
 response_status integer NOT NULL,
 response_body jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(actor_id,request_key)
);
CREATE INDEX mutation_receipts_age ON mutation_idempotency_receipts(created_at);
INSERT INTO schema_migrations(version,checksum) VALUES (3,'ea0947bd655fd442069541b8ff0e51b1276c60f64bdbe04b9750e94f798cfc41');
-- Completed immutable migration 003_fleet_inventory.sql

-- Beginning immutable migration 004_customer_foundation.sql (4ea9c5776fb94d8b53b80253c84222efe40a14d11420943ae33e3c6a88e75b4a)
-- Customer/application foundation for Charger on Wheels CMS.
-- Proposed migration 004, based on the applied immutable migrations 001-003.
-- Ownership: User BE. Pricing migration 005 requires this version to be applied.
-- Additive, no data backfill, no changes to existing tables, no HAL tables.
-- This file must be copied byte-for-byte into internal/database/migrations/ on promotion.

-- Every customer is exactly an 'app' principal. Separate customer_id from
-- account_id so jobs can retain stable domain references.
CREATE TABLE customer_profiles (
 id uuid PRIMARY KEY,
 account_id uuid NOT NULL UNIQUE,
 plane text NOT NULL DEFAULT 'app' CHECK (plane='app'),
 -- Existing Auth can provision an app principal with email/password only.
 -- Profile bootstrap must therefore be possible before collecting full_name.
 full_name text CHECK (full_name IS NULL OR length(btrim(full_name)) BETWEEN 1 AND 160),
 primary_phone text CHECK (primary_phone IS NULL OR length(primary_phone) BETWEEN 7 AND 32),
 status text NOT NULL DEFAULT 'INCOMPLETE'
   CHECK (status IN ('INCOMPLETE','ACTIVE','SUSPENDED','CLOSED')),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
 FOREIGN KEY (account_id,plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT,
 CHECK (status <> 'ACTIVE' OR full_name IS NOT NULL)
);
CREATE INDEX customer_profiles_status ON customer_profiles(status,created_at,id);

-- Saved vehicles belong to an application customer; VIN/registration is not
-- compulsory for an initial quote. Quoted vehicles are snapshotted separately.
CREATE TABLE customer_vehicles (
 id uuid PRIMARY KEY,
 customer_id uuid NOT NULL REFERENCES customer_profiles(id) ON DELETE RESTRICT,
 label text CHECK (label IS NULL OR length(btrim(label)) BETWEEN 1 AND 100),
 make text NOT NULL CHECK (length(btrim(make)) BETWEEN 1 AND 120),
 model text NOT NULL CHECK (length(btrim(model)) BETWEEN 1 AND 120),
 variant text,
 registration_number text COLLATE "C"
   CHECK (registration_number IS NULL OR
          (length(registration_number) BETWEEN 2 AND 32 AND registration_number=upper(registration_number))),
 battery_capacity_wh bigint CHECK (battery_capacity_wh IS NULL OR battery_capacity_wh > 0),
 supports_ac boolean,
 supports_dc boolean,
 max_ac_power_w integer CHECK (max_ac_power_w IS NULL OR max_ac_power_w > 0),
 max_dc_power_w integer CHECK (max_dc_power_w IS NULL OR max_dc_power_w > 0),
 connector_types text[] NOT NULL DEFAULT ARRAY[]::text[],
 is_active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
 CHECK (supports_ac IS DISTINCT FROM false OR max_ac_power_w IS NULL),
 CHECK (supports_dc IS DISTINCT FROM false OR max_dc_power_w IS NULL),
 UNIQUE(id,customer_id)
);
CREATE UNIQUE INDEX customer_vehicles_active_registration
 ON customer_vehicles(customer_id,registration_number)
 WHERE is_active AND registration_number IS NOT NULL;
CREATE INDEX customer_vehicles_list ON customer_vehicles(customer_id,is_active,created_at DESC,id);

-- Saved addresses may be inactive. Only one active default is permitted per
-- customer. Temporary location inputs for a request are not forced into this table.
CREATE TABLE customer_addresses (
 id uuid PRIMARY KEY,
 customer_id uuid NOT NULL REFERENCES customer_profiles(id) ON DELETE RESTRICT,
 label text CHECK (label IS NULL OR length(btrim(label)) BETWEEN 1 AND 80),
 line1 text NOT NULL CHECK (length(btrim(line1)) BETWEEN 1 AND 240),
 line2 text,
 locality text,
 city text NOT NULL CHECK (length(btrim(city)) BETWEEN 1 AND 120),
 state text NOT NULL CHECK (length(btrim(state)) BETWEEN 1 AND 120),
 postal_code text CHECK (postal_code IS NULL OR length(btrim(postal_code)) BETWEEN 3 AND 16),
 country_code text COLLATE "C" NOT NULL DEFAULT 'IN'
   CHECK (country_code ~ '^[A-Z]{2}$'),
 latitude numeric(9,6) CHECK (latitude BETWEEN -90 AND 90),
 longitude numeric(9,6) CHECK (longitude BETWEEN -180 AND 180),
 landmark text,
 building_instructions text,
 floor text,
 contact_instructions text,
 geocode_provider text,
 geocode_external_ref text,
 is_default boolean NOT NULL DEFAULT false,
 is_active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
 CHECK ((latitude IS NULL) = (longitude IS NULL)),
 CHECK (NOT is_default OR is_active),
 UNIQUE(id,customer_id)
);
CREATE UNIQUE INDEX customer_addresses_one_active_default
 ON customer_addresses(customer_id) WHERE is_active AND is_default;
CREATE INDEX customer_addresses_list ON customer_addresses(customer_id,is_active,created_at DESC,id);

-- Minimal change evidence, without duplicating sensitive address/phone contents
-- into permanent JSON history. Durable job snapshots are a separate future pack.
CREATE TABLE customer_master_audit (
 id bigserial PRIMARY KEY,
 customer_id uuid NOT NULL REFERENCES customer_profiles(id) ON DELETE RESTRICT,
 actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 resource_kind text NOT NULL CHECK (resource_kind IN ('PROFILE','VEHICLE','ADDRESS')),
 resource_id uuid NOT NULL,
 action text NOT NULL,
 resulting_version bigint NOT NULL CHECK (resulting_version > 0),
 changed_fields text[] NOT NULL DEFAULT ARRAY[]::text[],
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX customer_master_audit_customer ON customer_master_audit(customer_id,id DESC);
CREATE INDEX customer_master_audit_resource ON customer_master_audit(resource_kind,resource_id,id DESC);
INSERT INTO schema_migrations(version,checksum) VALUES (4,'4ea9c5776fb94d8b53b80253c84222efe40a14d11420943ae33e3c6a88e75b4a');
-- Completed immutable migration 004_customer_foundation.sql

-- Beginning immutable migration 005_admin_pricing_rules.sql (362de922de83e0661d8117cc8fd65c6c9c17ff1a028cae10f480c079180e729d)
-- Admin tariff revisions, publication topology and audit. Migration 004 is
-- reserved for the parallel User backend branch and must be merged first.
-- No hub identifier, guessed tax percentage, or live-job price is stored here.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version=4) THEN
  RAISE EXCEPTION 'Pricing migration 005 requires separately reviewed User migration 004 first';
 END IF;
END $$;
CREATE TABLE pricing_policies (
 id uuid PRIMARY KEY,
 policy_key text COLLATE "C" NOT NULL CHECK (policy_key ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 policy_version bigint NOT NULL CHECK (policy_version > 0),
 scope_kind text COLLATE "C" NOT NULL CHECK (scope_kind ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 scope_key text COLLATE "C" NOT NULL CHECK (scope_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$'),
 starts_at timestamptz,
 ends_at timestamptz,
 currency text NOT NULL CHECK (currency = 'INR'),
 rules jsonb NOT NULL CHECK (jsonb_typeof(rules)='array' AND jsonb_array_length(rules) BETWEEN 1 AND 128),
 lifecycle text NOT NULL DEFAULT 'DRAFT' CHECK (lifecycle IN ('DRAFT','PUBLISHED','WITHDRAWN')),
 state_version bigint NOT NULL DEFAULT 1 CHECK (state_version > 0),
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
 created_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 published_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 withdrawn_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 published_at timestamptz,
 withdrawn_at timestamptz,
 UNIQUE (policy_key,policy_version),
 CHECK ((starts_at IS NOT NULL OR ends_at IS NULL) AND (ends_at IS NULL OR starts_at < ends_at)),
 CHECK ((lifecycle='DRAFT' AND published_at IS NULL AND withdrawn_at IS NULL)
   OR (lifecycle='PUBLISHED' AND published_at IS NOT NULL AND withdrawn_at IS NULL)
   OR (lifecycle='WITHDRAWN' AND published_at IS NOT NULL AND withdrawn_at IS NOT NULL))
);
-- Fast read of exact scope; the publication transaction validates arbitrary
-- strictly nested bounded windows while serializing that scope under a lock.
CREATE INDEX pricing_scope_publication ON pricing_policies(scope_kind,scope_key,lifecycle,starts_at,ends_at);
CREATE INDEX pricing_latest_revision ON pricing_policies(policy_key,policy_version DESC);
CREATE UNIQUE INDEX pricing_published_one_root ON pricing_policies(scope_kind,scope_key)
 WHERE lifecycle='PUBLISHED' AND starts_at IS NULL;
CREATE UNIQUE INDEX pricing_published_one_open_start ON pricing_policies(scope_kind,scope_key,starts_at)
 WHERE lifecycle='PUBLISHED' AND starts_at IS NOT NULL AND ends_at IS NULL;

CREATE TABLE pricing_policy_audit (
 id bigserial PRIMARY KEY,
 policy_id uuid NOT NULL REFERENCES pricing_policies(id) ON DELETE RESTRICT,
 actor_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 action text NOT NULL CHECK (action IN ('DRAFT_CREATED','PUBLISHED','WITHDRAWN')),
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
 before_value jsonb,
 after_value jsonb NOT NULL,
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pricing_policy_audit_history ON pricing_policy_audit(policy_id,id DESC);
INSERT INTO schema_migrations(version,checksum) VALUES (5,'362de922de83e0661d8117cc8fd65c6c9c17ff1a028cae10f480c079180e729d');
-- Completed immutable migration 005_admin_pricing_rules.sql

-- Beginning immutable migration 006_ent_identity_bridge.sql (093d0bcc31a419cd8400ceeb7c26f86a8000f037246f7a2f88d03861efaaee12)
-- One-time additive identity bridge for Ent's entity IDs.
-- Preserve every existing business/natural key. The auth-plane FK is rebuilt
-- transactionally to reference the newly explicit UNIQUE(account_id, plane).
-- This migration is NOT an authorization to apply it to a live database.

ALTER TABLE auth_sessions DROP CONSTRAINT auth_sessions_account_id_plane_fkey;
ALTER TABLE customer_profiles DROP CONSTRAINT customer_profiles_account_id_plane_fkey;
ALTER TABLE admin_role_assignments DROP CONSTRAINT admin_role_assignments_account_id_plane_fkey;
ALTER TABLE admin_capability_overrides DROP CONSTRAINT admin_capability_overrides_account_id_plane_fkey;

ALTER TABLE principals ADD COLUMN id bigserial;
ALTER TABLE principals DROP CONSTRAINT principals_pkey;
ALTER TABLE principals ADD CONSTRAINT principals_pkey PRIMARY KEY (id);
ALTER TABLE principals ADD CONSTRAINT principals_account_id_plane_key UNIQUE (account_id, plane);

ALTER TABLE auth_sessions ADD CONSTRAINT auth_sessions_account_id_plane_fkey
    FOREIGN KEY (account_id, plane) REFERENCES principals(account_id, plane);
ALTER TABLE customer_profiles ADD CONSTRAINT customer_profiles_account_id_plane_fkey
    FOREIGN KEY (account_id, plane) REFERENCES principals(account_id, plane) ON DELETE RESTRICT;
ALTER TABLE admin_role_assignments ADD CONSTRAINT admin_role_assignments_account_id_plane_fkey
    FOREIGN KEY (account_id, plane) REFERENCES principals(account_id, plane);
ALTER TABLE admin_capability_overrides ADD CONSTRAINT admin_capability_overrides_account_id_plane_fkey
    FOREIGN KEY (account_id, plane) REFERENCES principals(account_id, plane);

ALTER TABLE consumed_refresh_tokens ADD COLUMN id bigserial;
ALTER TABLE consumed_refresh_tokens DROP CONSTRAINT consumed_refresh_tokens_pkey;
ALTER TABLE consumed_refresh_tokens ADD CONSTRAINT consumed_refresh_tokens_pkey PRIMARY KEY (id);
ALTER TABLE consumed_refresh_tokens ADD CONSTRAINT consumed_refresh_tokens_digest_key UNIQUE (digest);

ALTER TABLE auth_action_tokens ADD COLUMN id bigserial;
ALTER TABLE auth_action_tokens DROP CONSTRAINT auth_action_tokens_pkey;
ALTER TABLE auth_action_tokens ADD CONSTRAINT auth_action_tokens_pkey PRIMARY KEY (id);
ALTER TABLE auth_action_tokens ADD CONSTRAINT auth_action_tokens_digest_key UNIQUE (digest);

ALTER TABLE auth_rate_limits ADD COLUMN id bigserial;
ALTER TABLE auth_rate_limits DROP CONSTRAINT auth_rate_limits_pkey;
ALTER TABLE auth_rate_limits ADD CONSTRAINT auth_rate_limits_pkey PRIMARY KEY (id);
ALTER TABLE auth_rate_limits ADD CONSTRAINT auth_rate_limits_bucket_key_digest_key UNIQUE (bucket, key_digest);

ALTER TABLE admin_role_assignments ADD COLUMN id bigserial;
ALTER TABLE admin_role_assignments DROP CONSTRAINT admin_role_assignments_pkey;
ALTER TABLE admin_role_assignments ADD CONSTRAINT admin_role_assignments_pkey PRIMARY KEY (id);
ALTER TABLE admin_role_assignments ADD CONSTRAINT admin_role_assignments_account_id_role_key_key UNIQUE (account_id, role_key);

ALTER TABLE admin_capability_overrides ADD COLUMN id bigserial;
ALTER TABLE admin_capability_overrides DROP CONSTRAINT admin_capability_overrides_pkey;
ALTER TABLE admin_capability_overrides ADD CONSTRAINT admin_capability_overrides_pkey PRIMARY KEY (id);
ALTER TABLE admin_capability_overrides ADD CONSTRAINT admin_capability_overrides_account_id_capability_key UNIQUE (account_id, capability);

ALTER TABLE mutation_idempotency_receipts ADD COLUMN id bigserial;
ALTER TABLE mutation_idempotency_receipts DROP CONSTRAINT mutation_idempotency_receipts_pkey;
ALTER TABLE mutation_idempotency_receipts ADD CONSTRAINT mutation_idempotency_receipts_pkey PRIMARY KEY (id);
ALTER TABLE mutation_idempotency_receipts ADD CONSTRAINT mutation_idempotency_receipts_actor_id_request_key_key UNIQUE (actor_id, request_key);
INSERT INTO schema_migrations(version,checksum) VALUES (6,'093d0bcc31a419cd8400ceeb7c26f86a8000f037246f7a2f88d03861efaaee12');
-- Completed immutable migration 006_ent_identity_bridge.sql
