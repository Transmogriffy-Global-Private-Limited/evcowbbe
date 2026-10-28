-- TARGET-STATE REFERENCE DDL ONLY; no automatic application. Requires CMS
-- 004 customer, 005 pricing, and sql/cms/10_workforce_zones_assets.sql.

CREATE TABLE service_requests (
 id uuid PRIMARY KEY, customer_id uuid NOT NULL REFERENCES customer_profiles(id) ON DELETE RESTRICT,
 client_key text NOT NULL CHECK(length(client_key) BETWEEN 16 AND 128),
 saved_vehicle_id uuid REFERENCES customer_vehicles(id) ON DELETE RESTRICT,
 saved_address_id uuid REFERENCES customer_addresses(id) ON DELETE RESTRICT,
 vehicle_input jsonb NOT NULL, destination_input jsonb NOT NULL,
 requested_start_at timestamptz, service_zone_id uuid REFERENCES service_zones(id) ON DELETE RESTRICT,
 state text NOT NULL CHECK(state IN ('DRAFT','QUOTING','QUOTED','BOOKED','CANCELLED','EXPIRED')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 UNIQUE(customer_id,client_key), UNIQUE(id,customer_id),
 FOREIGN KEY(saved_vehicle_id,customer_id) REFERENCES customer_vehicles(id,customer_id) ON DELETE RESTRICT,
 FOREIGN KEY(saved_address_id,customer_id) REFERENCES customer_addresses(id,customer_id) ON DELETE RESTRICT
);
CREATE INDEX service_requests_customer ON service_requests(customer_id,created_at DESC,id);
-- Saved vehicle/address ownership must be validated in the same transaction;
-- app layer must never trust a foreign ID from the JSON body.

CREATE TABLE quotes (
 id uuid PRIMARY KEY, request_id uuid NOT NULL REFERENCES service_requests(id) ON DELETE RESTRICT,
 revision bigint NOT NULL CHECK(revision>0), state text NOT NULL
 CHECK(state IN ('OFFERED','ACCEPTED','EXPIRED','REVOKED')),
 expires_at timestamptz NOT NULL, accepted_at timestamptz,
 amount_estimate_paise bigint NOT NULL CHECK(amount_estimate_paise>=0),
 currency text NOT NULL CHECK(currency='INR'),
 terms_schema_version integer NOT NULL CHECK(terms_schema_version>0),
 terms_snapshot jsonb NOT NULL, pricing_policy_id uuid NOT NULL REFERENCES pricing_policies(id) ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(request_id,revision), UNIQUE(id,request_id),
 CHECK((state='ACCEPTED')=(accepted_at IS NOT NULL))
);
CREATE INDEX quotes_request ON quotes(request_id,revision DESC);
CREATE UNIQUE INDEX quotes_one_accepted_request ON quotes(request_id) WHERE state='ACCEPTED';

CREATE TABLE service_jobs (
 id uuid PRIMARY KEY, request_id uuid NOT NULL UNIQUE, customer_id uuid NOT NULL,
 accepted_quote_id uuid NOT NULL UNIQUE, public_reference text COLLATE "C" NOT NULL UNIQUE,
 state text NOT NULL CHECK(state IN
 ('DISPATCHING','ASSIGNED','ACCEPTED','EN_ROUTE','ARRIVED','SERVICE_PREPARING','CHARGING','WRAP_UP','COMPLETED','REASSIGNING','CANCELLED','FAILED','NO_SHOW','EXPIRED')),
 requested_start_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 terminal_at timestamptz, version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 FOREIGN KEY(request_id,customer_id) REFERENCES service_requests(id,customer_id) ON DELETE RESTRICT,
 FOREIGN KEY(accepted_quote_id,request_id) REFERENCES quotes(id,request_id) ON DELETE RESTRICT
);
CREATE INDEX service_jobs_customer ON service_jobs(customer_id,created_at DESC,id);
CREATE INDEX service_jobs_operations ON service_jobs(state,requested_start_at,id);

CREATE TABLE service_job_events (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 event_seq bigint NOT NULL CHECK(event_seq>0), from_state text, to_state text NOT NULL,
 source text NOT NULL, actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 reason_code text, evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 causation_key text, occurred_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(job_id,event_seq)
);
CREATE INDEX service_job_events_history ON service_job_events(job_id,event_seq DESC);

CREATE TABLE service_job_vehicle_snapshots (
 job_id uuid PRIMARY KEY REFERENCES service_jobs(id) ON DELETE RESTRICT,
 source_vehicle_id uuid REFERENCES customer_vehicles(id) ON DELETE RESTRICT,
 source_master_version bigint,
 schema_version integer NOT NULL CHECK(schema_version>0),
 snapshot jsonb NOT NULL, captured_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE service_job_address_snapshots (
 job_id uuid PRIMARY KEY REFERENCES service_jobs(id) ON DELETE RESTRICT,
 source_address_id uuid REFERENCES customer_addresses(id) ON DELETE RESTRICT,
 source_master_version bigint,
 schema_version integer NOT NULL CHECK(schema_version>0),
 snapshot jsonb NOT NULL, captured_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dispatch_attempts (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 attempt_no integer NOT NULL CHECK(attempt_no>0),
 state text NOT NULL CHECK(state IN ('RUNNING','SUCCEEDED','EXHAUSTED','CANCELLED')),
 service_zone_version_id uuid REFERENCES service_zone_versions(id) ON DELETE RESTRICT,
 policy_digest bytea, trigger_reason text NOT NULL,
 started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 lease_token uuid, lease_until timestamptz, UNIQUE(job_id,attempt_no),
 CHECK(finished_at IS NULL OR finished_at>=started_at)
);
CREATE INDEX dispatch_attempts_due ON dispatch_attempts(state,lease_until,started_at);
CREATE UNIQUE INDEX dispatch_one_running_attempt ON dispatch_attempts(job_id) WHERE state='RUNNING';

CREATE TABLE dispatch_candidates (
 id uuid PRIMARY KEY, attempt_id uuid NOT NULL REFERENCES dispatch_attempts(id) ON DELETE RESTRICT,
 driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 duty_id uuid REFERENCES driver_duty_sessions(id) ON DELETE RESTRICT,
 candidate_rank integer CHECK(candidate_rank IS NULL OR candidate_rank>0),
 eligibility_state text NOT NULL CHECK(eligibility_state IN ('ELIGIBLE','INELIGIBLE')),
 reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
 evidence_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
 evaluated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(attempt_id,driver_id), UNIQUE(id,attempt_id,driver_id)
);

CREATE TABLE dispatch_offers (
 id uuid PRIMARY KEY, attempt_id uuid NOT NULL REFERENCES dispatch_attempts(id) ON DELETE RESTRICT,
 candidate_id uuid NOT NULL REFERENCES dispatch_candidates(id) ON DELETE RESTRICT,
 driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 offer_token uuid NOT NULL UNIQUE,
 state text NOT NULL CHECK(state IN ('OFFERED','ACCEPTED','DECLINED','EXPIRED','CANCELLED')),
 offered_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 responded_at timestamptz, reason text, version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 CHECK(expires_at>offered_at),
 CHECK(state NOT IN ('ACCEPTED','DECLINED') OR responded_at IS NOT NULL),
 FOREIGN KEY(candidate_id,attempt_id,driver_id)
  REFERENCES dispatch_candidates(id,attempt_id,driver_id) ON DELETE RESTRICT
);
CREATE INDEX dispatch_offers_driver ON dispatch_offers(driver_id,state,expires_at);
CREATE UNIQUE INDEX dispatch_one_accepted_offer ON dispatch_offers(attempt_id) WHERE state='ACCEPTED';

CREATE TABLE job_assignments (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 duty_id uuid NOT NULL REFERENCES driver_duty_sessions(id) ON DELETE RESTRICT,
 accepted_offer_id uuid UNIQUE REFERENCES dispatch_offers(id) ON DELETE RESTRICT,
 vehicle_id uuid NOT NULL REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 charger_id uuid NOT NULL REFERENCES chargers(id) ON DELETE RESTRICT,
 state text NOT NULL CHECK(state IN ('ACTIVE','COMPLETED','REASSIGNED','CANCELLED')),
 assigned_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz,
 end_reason text, version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 CHECK((state='ACTIVE')=(ended_at IS NULL)),
 UNIQUE(id,job_id),
 FOREIGN KEY(duty_id,driver_id) REFERENCES driver_duty_sessions(id,driver_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX job_one_active_assignment ON job_assignments(job_id) WHERE state='ACTIVE';
CREATE UNIQUE INDEX driver_one_active_job ON job_assignments(driver_id) WHERE state='ACTIVE';
CREATE INDEX job_assignments_history ON job_assignments(job_id,assigned_at DESC);
