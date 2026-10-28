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
