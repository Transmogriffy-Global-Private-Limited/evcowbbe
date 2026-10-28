-- GATED TARGET-STATE REFERENCE DDL ONLY. DO NOT PROMOTE unless hardware
-- decision explicitly declares mobile energy storage REQUIRED. Never infer
-- energy capacity from a vehicle/charger catalog row.

CREATE TABLE mobile_energy_stores (
 id uuid PRIMARY KEY, asset_tag text NOT NULL UNIQUE,
 attached_vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 attached_charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 rated_capacity_wh bigint NOT NULL CHECK(rated_capacity_wh>0),
 operational_status text NOT NULL CHECK(operational_status IN ('AVAILABLE','DEGRADED','OUT_OF_SERVICE','RETIRED')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0)
);

CREATE TABLE mobile_energy_observations (
 id uuid PRIMARY KEY, store_id uuid NOT NULL REFERENCES mobile_energy_stores(id) ON DELETE RESTRICT,
 source_fact_key text NOT NULL, available_wh bigint NOT NULL CHECK(available_wh>=0),
 observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
 evidence_sha256 bytea NOT NULL CHECK(octet_length(evidence_sha256)=32),
 UNIQUE(store_id,source_fact_key)
);
CREATE INDEX mobile_energy_obs_latest ON mobile_energy_observations(store_id,observed_at DESC);

CREATE TABLE mobile_energy_state (
 store_id uuid PRIMARY KEY REFERENCES mobile_energy_stores(id) ON DELETE RESTRICT,
 latest_observation_id uuid NOT NULL REFERENCES mobile_energy_observations(id) ON DELETE RESTRICT,
 observed_available_wh bigint NOT NULL CHECK(observed_available_wh>=0),
 reserved_wh bigint NOT NULL DEFAULT 0 CHECK(reserved_wh>=0),
 projection_version bigint NOT NULL DEFAULT 1 CHECK(projection_version>0),
 CHECK(reserved_wh<=observed_available_wh)
);

CREATE TABLE mobile_energy_reservations (
 id uuid PRIMARY KEY, store_id uuid NOT NULL REFERENCES mobile_energy_stores(id) ON DELETE RESTRICT,
 job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 reserved_wh bigint NOT NULL CHECK(reserved_wh>0),
 state text NOT NULL CHECK(state IN ('ACTIVE','CONSUMED','RELEASED','EXPIRED')),
 created_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz,
 CHECK((state='ACTIVE')=(closed_at IS NULL))
);
CREATE UNIQUE INDEX mobile_energy_one_active_job ON mobile_energy_reservations(job_id) WHERE state='ACTIVE';
CREATE INDEX mobile_energy_reserved_store ON mobile_energy_reservations(store_id,state,created_at);
-- Lock mobile_energy_state FOR UPDATE and atomically adjust reserved_wh;
-- row CHECK prevents negative available balance but not stale observation errors.
