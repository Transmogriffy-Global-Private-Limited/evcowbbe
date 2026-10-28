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
