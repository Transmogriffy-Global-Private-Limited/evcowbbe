-- HAL TARGET-STATE REFERENCE DDL ONLY. Separate PostgreSQL database.
-- NEVER install in CMS evcowbbe database, never FK to CMS tables.
-- The HAL service is responsible for physical charger/protocol source truth.

CREATE TABLE charger_mappings (
 id uuid PRIMARY KEY, cms_charger_id uuid NOT NULL,
 protocol_family text NOT NULL, protocol_charger_identity text NOT NULL,
 mapping_version bigint NOT NULL CHECK(mapping_version>0),
 state text NOT NULL CHECK(state IN ('ACTIVE','SUPERSEDED','REVOKED')),
 effective_from timestamptz NOT NULL DEFAULT now(), effective_to timestamptz,
 configuration_digest bytea NOT NULL CHECK(octet_length(configuration_digest)=32),
 acknowledged_at timestamptz, UNIQUE(cms_charger_id,mapping_version),
 CHECK((state='ACTIVE')=(effective_to IS NULL))
);
CREATE UNIQUE INDEX hal_one_active_cms_mapping ON charger_mappings(cms_charger_id) WHERE state='ACTIVE';
CREATE UNIQUE INDEX hal_one_active_protocol_identity ON charger_mappings(protocol_family,protocol_charger_identity)
 WHERE state='ACTIVE';

CREATE TABLE charger_connections (
 id uuid PRIMARY KEY, mapping_id uuid NOT NULL REFERENCES charger_mappings(id) ON DELETE RESTRICT,
 connection_nonce uuid NOT NULL UNIQUE, protocol_version text NOT NULL,
 remote_identity text, authenticated_at timestamptz,
 connected_at timestamptz NOT NULL DEFAULT now(), disconnected_at timestamptz,
 disconnect_reason text,
 CHECK(disconnected_at IS NULL OR disconnected_at>=connected_at)
);
CREATE INDEX hal_connections_mapping ON charger_connections(mapping_id,connected_at DESC);
CREATE UNIQUE INDEX hal_one_open_connection ON charger_connections(mapping_id) WHERE disconnected_at IS NULL;

CREATE TABLE connector_status_observations (
 id uuid PRIMARY KEY, mapping_id uuid NOT NULL REFERENCES charger_mappings(id) ON DELETE RESTRICT,
 connection_id uuid REFERENCES charger_connections(id) ON DELETE RESTRICT,
 connector_number integer NOT NULL CHECK(connector_number>0),
 observed_status text NOT NULL, source_message_key text NOT NULL,
 source_sha256 bytea NOT NULL CHECK(octet_length(source_sha256)=32),
 charger_observed_at timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(mapping_id,source_message_key)
);
CREATE INDEX hal_connector_status_latest ON connector_status_observations(mapping_id,connector_number,received_at DESC);

CREATE TABLE hal_commands (
 id uuid PRIMARY KEY, cms_command_id uuid NOT NULL UNIQUE,
 mapping_id uuid NOT NULL REFERENCES charger_mappings(id) ON DELETE RESTRICT,
 command_kind text NOT NULL, schema_version integer NOT NULL CHECK(schema_version>0),
 payload jsonb NOT NULL, request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 state text NOT NULL CHECK(state IN ('RECEIVED','VALIDATED','QUEUED','SENT','ACKED','REJECTED','TIMED_OUT')),
 received_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 outcome jsonb
);
CREATE INDEX hal_commands_due ON hal_commands(state,received_at);

CREATE TABLE hal_command_delivery_attempts (
 id uuid PRIMARY KEY, command_id uuid NOT NULL REFERENCES hal_commands(id) ON DELETE RESTRICT,
 attempt_no integer NOT NULL CHECK(attempt_no>0),
 connection_id uuid REFERENCES charger_connections(id) ON DELETE RESTRICT,
 state text NOT NULL CHECK(state IN ('SENT','ACKED','FAILED','TIMED_OUT')),
 sent_at timestamptz NOT NULL DEFAULT now(), response_at timestamptz,
 response_digest bytea, error_class text, UNIQUE(command_id,attempt_no)
);

CREATE TABLE hal_transactions (
 id uuid PRIMARY KEY, mapping_id uuid NOT NULL REFERENCES charger_mappings(id) ON DELETE RESTRICT,
 protocol_transaction_key text NOT NULL, connector_number integer NOT NULL CHECK(connector_number>0),
 originating_command_id uuid REFERENCES hal_commands(id) ON DELETE RESTRICT,
 physical_state text NOT NULL CHECK(physical_state IN ('STARTED','STOPPING','STOPPED','INTERRUPTED')),
 started_at timestamptz NOT NULL, ended_at timestamptz,
 start_meter_wh bigint CHECK(start_meter_wh IS NULL OR start_meter_wh>=0),
 end_meter_wh bigint CHECK(end_meter_wh IS NULL OR end_meter_wh>=0),
 start_source_digest bytea NOT NULL CHECK(octet_length(start_source_digest)=32),
 end_source_digest bytea,
 UNIQUE(mapping_id,protocol_transaction_key), UNIQUE(id,mapping_id),
 CHECK(ended_at IS NULL OR ended_at>=started_at),
 CHECK(end_meter_wh IS NULL OR start_meter_wh IS NULL OR end_meter_wh>=start_meter_wh)
);
CREATE INDEX hal_transactions_mapping ON hal_transactions(mapping_id,started_at DESC);

CREATE TABLE hal_meter_observations (
 id uuid PRIMARY KEY, transaction_id uuid NOT NULL REFERENCES hal_transactions(id) ON DELETE RESTRICT,
 source_sequence bigint NOT NULL CHECK(source_sequence>0),
 meter_wh bigint NOT NULL CHECK(meter_wh>=0),
 charger_observed_at timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
 source_sha256 bytea NOT NULL CHECK(octet_length(source_sha256)=32),
 UNIQUE(transaction_id,source_sequence)
);
CREATE INDEX hal_meter_history ON hal_meter_observations(transaction_id,source_sequence DESC);

CREATE TABLE hal_soc_observations (
 id uuid PRIMARY KEY, transaction_id uuid NOT NULL REFERENCES hal_transactions(id) ON DELETE RESTRICT,
 source_sequence bigint NOT NULL CHECK(source_sequence>0),
 soc_percent numeric(5,2) NOT NULL CHECK(soc_percent BETWEEN 0 AND 100),
 charger_observed_at timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
 source_sha256 bytea NOT NULL CHECK(octet_length(source_sha256)=32),
 UNIQUE(transaction_id,source_sequence)
);

-- HAL emits one canonical transport envelope per physical fact. CMS stores a
-- copy under (hal_instance,fact_id), validates digest and monotonic ordering.
CREATE TABLE hal_facts (
 id uuid PRIMARY KEY, mapping_id uuid NOT NULL REFERENCES charger_mappings(id) ON DELETE RESTRICT,
 transaction_id uuid REFERENCES hal_transactions(id) ON DELETE RESTRICT,
 fact_sequence bigint NOT NULL CHECK(fact_sequence>0),
 fact_kind text NOT NULL, schema_version integer NOT NULL CHECK(schema_version>0),
 payload jsonb NOT NULL, payload_sha256 bytea NOT NULL CHECK(octet_length(payload_sha256)=32),
 occurred_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(mapping_id,fact_sequence),
 FOREIGN KEY(transaction_id,mapping_id) REFERENCES hal_transactions(id,mapping_id) ON DELETE RESTRICT
);
CREATE INDEX hal_facts_transaction ON hal_facts(transaction_id,fact_sequence);

CREATE TABLE hal_fact_outbox (
 fact_id uuid PRIMARY KEY REFERENCES hal_facts(id) ON DELETE RESTRICT,
 state text NOT NULL CHECK(state IN ('PENDING','LEASED','DELIVERED','DEAD')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, lease_until timestamptz, cms_ack_at timestamptz, last_error_class text
);
CREATE INDEX hal_fact_outbox_due ON hal_fact_outbox(state,next_attempt_at);

CREATE TABLE hal_trace_events (
 id uuid PRIMARY KEY, mapping_id uuid REFERENCES charger_mappings(id) ON DELETE RESTRICT,
 connection_id uuid REFERENCES charger_connections(id) ON DELETE RESTRICT,
 transaction_id uuid REFERENCES hal_transactions(id) ON DELETE RESTRICT,
 trace_kind text NOT NULL, direction text NOT NULL CHECK(direction IN ('INBOUND','OUTBOUND','INTERNAL')),
 redacted_payload jsonb NOT NULL, payload_sha256 bytea NOT NULL CHECK(octet_length(payload_sha256)=32),
 occurred_at timestamptz NOT NULL DEFAULT now(),
 retention_class text NOT NULL
);
CREATE INDEX hal_trace_transactions ON hal_trace_events(transaction_id,occurred_at DESC);

CREATE TABLE hal_trace_outbox (
 trace_id uuid PRIMARY KEY REFERENCES hal_trace_events(id) ON DELETE RESTRICT,
 state text NOT NULL CHECK(state IN ('PENDING','LEASED','DELIVERED','DEAD')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, lease_until timestamptz, delivered_at timestamptz, last_error_class text
);
CREATE INDEX hal_trace_outbox_due ON hal_trace_outbox(state,next_attempt_at);

CREATE TABLE charger_operations (
 id uuid PRIMARY KEY, mapping_id uuid NOT NULL REFERENCES charger_mappings(id) ON DELETE RESTRICT,
 command_id uuid REFERENCES hal_commands(id) ON DELETE RESTRICT,
 operation_kind text NOT NULL, requested_by text NOT NULL,
 safety_interlock_snapshot jsonb NOT NULL,
 outcome text NOT NULL CHECK(outcome IN ('ALLOWED','DENIED','EXECUTED','FAILED')),
 reason_code text, occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX charger_operations_charger ON charger_operations(mapping_id,occurred_at DESC);

CREATE TABLE recovery_leases (
 recovery_key text PRIMARY KEY, lease_token uuid NOT NULL,
 lease_owner text NOT NULL, lease_until timestamptz NOT NULL,
 last_cursor text, last_success_at timestamptz,
 version bigint NOT NULL DEFAULT 1 CHECK(version>0)
);
