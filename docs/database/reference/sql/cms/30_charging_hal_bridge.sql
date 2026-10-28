-- TARGET-STATE REFERENCE DDL ONLY. CMS command and received physical facts.
-- Requires CMS 003 and job/dispatch tables from 20_requests_jobs_dispatch.sql.
-- HAL maintains its OWN PostgreSQL DB; never fabricate physical facts here.

CREATE TABLE cms_hal_commands (
 id uuid PRIMARY KEY, command_id uuid NOT NULL UNIQUE,
 charger_id uuid NOT NULL REFERENCES chargers(id) ON DELETE RESTRICT,
 command_kind text NOT NULL, intent_schema_version integer NOT NULL CHECK(intent_schema_version>0),
 intent_payload jsonb NOT NULL, intent_sha256 bytea NOT NULL CHECK(octet_length(intent_sha256)=32),
 state text NOT NULL CHECK(state IN ('PENDING','LEASED','ACKNOWLEDGED','REJECTED','DEAD')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, lease_until timestamptz, outcome_payload jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cms_hal_commands_due ON cms_hal_commands(state,next_attempt_at,id);

CREATE TABLE cms_hal_fact_receipts (
 id uuid PRIMARY KEY, hal_instance text NOT NULL, fact_id uuid NOT NULL,
 charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 hal_sequence bigint NOT NULL CHECK(hal_sequence>0),
 kind text NOT NULL, schema_version integer NOT NULL CHECK(schema_version>0),
 payload_sha256 bytea NOT NULL CHECK(octet_length(payload_sha256)=32),
 fact_payload jsonb NOT NULL, emitted_at timestamptz NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now(), processed_at timestamptz,
 processing_error_class text, UNIQUE(hal_instance,fact_id)
);
CREATE INDEX cms_hal_fact_unprocessed ON cms_hal_fact_receipts(received_at,id) WHERE processed_at IS NULL;
CREATE INDEX cms_hal_fact_charger_seq ON cms_hal_fact_receipts(hal_instance,charger_id,hal_sequence);
-- Duplicate fact identity with different digest is a protocol-integrity incident,
-- not an update of the previously accepted payload.

CREATE TABLE charging_start_intents (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 assignment_id uuid NOT NULL REFERENCES job_assignments(id) ON DELETE RESTRICT,
 connector_id uuid NOT NULL REFERENCES charger_connectors(id) ON DELETE RESTRICT,
 actor_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
 request_key text NOT NULL CHECK(length(request_key) BETWEEN 16 AND 128),
 command_id uuid UNIQUE REFERENCES cms_hal_commands(id) ON DELETE RESTRICT,
 state text NOT NULL CHECK(state IN ('REQUESTED','SENT','CONFIRMED','REJECTED','EXPIRED')),
 requested_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 precondition_snapshot jsonb NOT NULL, UNIQUE(actor_account_id,request_key),
 CHECK(expires_at>requested_at),
 FOREIGN KEY(assignment_id,job_id) REFERENCES job_assignments(id,job_id) ON DELETE RESTRICT
);

CREATE TABLE charging_sessions (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 assignment_id uuid NOT NULL REFERENCES job_assignments(id) ON DELETE RESTRICT,
 start_intent_id uuid UNIQUE REFERENCES charging_start_intents(id) ON DELETE RESTRICT,
 charger_id uuid NOT NULL REFERENCES chargers(id) ON DELETE RESTRICT,
 connector_id uuid NOT NULL REFERENCES charger_connectors(id) ON DELETE RESTRICT,
 hal_instance text NOT NULL, hal_transaction_key text NOT NULL,
 start_fact_receipt_id uuid NOT NULL UNIQUE REFERENCES cms_hal_fact_receipts(id) ON DELETE RESTRICT,
 stop_fact_receipt_id uuid UNIQUE REFERENCES cms_hal_fact_receipts(id) ON DELETE RESTRICT,
 state text NOT NULL CHECK(state IN ('ACTIVE','ENDING','ENDED','INTERRUPTED','FAILED')),
 started_at timestamptz NOT NULL, ended_at timestamptz,
 start_meter_wh bigint CHECK(start_meter_wh IS NULL OR start_meter_wh>=0),
 end_meter_wh bigint CHECK(end_meter_wh IS NULL OR end_meter_wh>=0),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 UNIQUE(hal_instance,hal_transaction_key),
 CHECK(ended_at IS NULL OR ended_at>=started_at),
 CHECK(end_meter_wh IS NULL OR start_meter_wh IS NULL OR end_meter_wh>=start_meter_wh),
 FOREIGN KEY(assignment_id,job_id) REFERENCES job_assignments(id,job_id) ON DELETE RESTRICT
);
CREATE INDEX charging_sessions_job ON charging_sessions(job_id,started_at DESC,id);
CREATE UNIQUE INDEX charging_one_active_connector ON charging_sessions(connector_id)
 WHERE state IN ('ACTIVE','ENDING');

CREATE TABLE charging_stop_requests (
 id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES charging_sessions(id) ON DELETE RESTRICT,
 actor_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
 request_key text NOT NULL CHECK(length(request_key) BETWEEN 16 AND 128),
 command_id uuid UNIQUE REFERENCES cms_hal_commands(id) ON DELETE RESTRICT,
 reason_code text NOT NULL, state text NOT NULL
 CHECK(state IN ('REQUESTED','SENT','CONFIRMED','REJECTED')),
 requested_at timestamptz NOT NULL DEFAULT now(), UNIQUE(actor_account_id,request_key)
);

CREATE TABLE charging_meter_observations (
 id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES charging_sessions(id) ON DELETE RESTRICT,
 source_fact_receipt_id uuid NOT NULL UNIQUE REFERENCES cms_hal_fact_receipts(id) ON DELETE RESTRICT,
 meter_wh bigint NOT NULL CHECK(meter_wh>=0),
 observed_at timestamptz NOT NULL, source_sequence bigint NOT NULL CHECK(source_sequence>0),
 recorded_at timestamptz NOT NULL DEFAULT now(), UNIQUE(session_id,source_sequence)
);
CREATE INDEX charging_meter_session ON charging_meter_observations(session_id,source_sequence DESC);

CREATE TABLE charging_soc_observations (
 id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES charging_sessions(id) ON DELETE RESTRICT,
 source_fact_receipt_id uuid NOT NULL UNIQUE REFERENCES cms_hal_fact_receipts(id) ON DELETE RESTRICT,
 soc_percent numeric(5,2) NOT NULL CHECK(soc_percent BETWEEN 0 AND 100),
 observed_at timestamptz NOT NULL, source_sequence bigint NOT NULL CHECK(source_sequence>0),
 recorded_at timestamptz NOT NULL DEFAULT now(), UNIQUE(session_id,source_sequence)
);

CREATE TABLE charger_runtime_projection (
 charger_id uuid PRIMARY KEY REFERENCES chargers(id) ON DELETE RESTRICT,
 runtime_state text NOT NULL, hal_instance text, last_hal_sequence bigint,
 last_evidence_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
 projection_version bigint NOT NULL DEFAULT 1 CHECK(projection_version>0)
);
CREATE TABLE connector_runtime_projection (
 connector_id uuid PRIMARY KEY REFERENCES charger_connectors(id) ON DELETE RESTRICT,
 runtime_state text NOT NULL, last_fact_receipt_id uuid REFERENCES cms_hal_fact_receipts(id) ON DELETE RESTRICT,
 updated_at timestamptz NOT NULL DEFAULT now(),
 projection_version bigint NOT NULL DEFAULT 1 CHECK(projection_version>0)
);
CREATE TABLE charging_runtime_projection (
 session_id uuid PRIMARY KEY REFERENCES charging_sessions(id) ON DELETE RESTRICT,
 last_meter_observation_id uuid REFERENCES charging_meter_observations(id) ON DELETE RESTRICT,
 last_soc_observation_id uuid REFERENCES charging_soc_observations(id) ON DELETE RESTRICT,
 delivered_wh bigint CHECK(delivered_wh IS NULL OR delivered_wh>=0),
 updated_at timestamptz NOT NULL DEFAULT now(), projection_version bigint NOT NULL DEFAULT 1 CHECK(projection_version>0)
);
CREATE TABLE charger_mapping_delivery_state (
 charger_id uuid PRIMARY KEY REFERENCES chargers(id) ON DELETE RESTRICT,
 desired_mapping_version bigint NOT NULL CHECK(desired_mapping_version>0),
 acknowledged_mapping_version bigint CHECK(acknowledged_mapping_version>0),
 delivery_state text NOT NULL CHECK(delivery_state IN ('PENDING','ACKNOWLEDGED','DRIFT','FAILED')),
 next_attempt_at timestamptz, last_ack_at timestamptz,
 updated_at timestamptz NOT NULL DEFAULT now()
);
