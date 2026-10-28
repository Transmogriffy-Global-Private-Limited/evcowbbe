-- TARGET-STATE REFERENCE DDL ONLY. NOT an executable numbered migration.
-- Depends on CMS 001-004 and existing Fleet 003. All policies require review.

CREATE TABLE service_zones (
 id uuid PRIMARY KEY, zone_key text COLLATE "C" NOT NULL UNIQUE,
 display_name text NOT NULL, is_active boolean NOT NULL DEFAULT false,
 current_version bigint, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), version bigint NOT NULL DEFAULT 1 CHECK(version>0)
);
CREATE TABLE service_zone_versions (
 id uuid PRIMARY KEY, zone_id uuid NOT NULL REFERENCES service_zones(id) ON DELETE RESTRICT,
 version bigint NOT NULL CHECK(version>0), definition_kind text NOT NULL CHECK(definition_kind IN ('RADIUS','POLYGON')),
 definition jsonb NOT NULL, effective_from timestamptz NOT NULL,
 effective_to timestamptz, published_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 published_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(zone_id,version), CHECK(effective_to IS NULL OR effective_to>effective_from)
);
CREATE INDEX service_zone_versions_period ON service_zone_versions(zone_id,effective_from DESC);
-- Publication code must serialize zone scope and prohibit conflicting active windows.

CREATE TABLE driver_applications (
 id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
 status text NOT NULL CHECK(status IN ('DRAFT','SUBMITTED','UNDER_REVIEW','MORE_INFORMATION','APPROVED','REJECTED','WITHDRAWN')),
 application_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 consent_version text, submitted_at timestamptz, reviewed_at timestamptz,
 reviewed_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0)
);
CREATE INDEX driver_applications_account ON driver_applications(account_id,created_at DESC);
CREATE UNIQUE INDEX driver_one_open_application ON driver_applications(account_id)
 WHERE status IN ('DRAFT','SUBMITTED','UNDER_REVIEW','MORE_INFORMATION');

CREATE TABLE driver_application_history (
 id uuid PRIMARY KEY, application_id uuid NOT NULL REFERENCES driver_applications(id) ON DELETE RESTRICT,
 event_seq bigint NOT NULL CHECK(event_seq>0), from_status text, to_status text NOT NULL,
 actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 reason text, occurred_at timestamptz NOT NULL DEFAULT now(), UNIQUE(application_id,event_seq)
);

CREATE TABLE driver_profiles (
 id uuid PRIMARY KEY, account_id uuid NOT NULL UNIQUE, plane text NOT NULL DEFAULT 'driver' CHECK(plane='driver'),
 source_application_id uuid UNIQUE REFERENCES driver_applications(id) ON DELETE RESTRICT,
 display_name text NOT NULL, status text NOT NULL DEFAULT 'INACTIVE'
 CHECK(status IN ('INACTIVE','ELIGIBLE','SUSPENDED','RETIRED')),
 activated_at timestamptz, suspended_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT
);

CREATE TABLE driver_documents (
 id uuid PRIMARY KEY, application_id uuid REFERENCES driver_applications(id) ON DELETE RESTRICT,
 driver_id uuid REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 document_kind text NOT NULL, object_storage_key text NOT NULL, content_sha256 bytea NOT NULL
 CHECK(octet_length(content_sha256)=32), expiry_date date,
 status text NOT NULL DEFAULT 'SUBMITTED' CHECK(status IN ('SUBMITTED','ACCEPTED','REJECTED','SUPERSEDED')),
 submitted_at timestamptz NOT NULL DEFAULT now(),
 CHECK(application_id IS NOT NULL OR driver_id IS NOT NULL)
);
CREATE INDEX driver_documents_driver ON driver_documents(driver_id,document_kind,submitted_at DESC);

CREATE TABLE driver_document_reviews (
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES driver_documents(id) ON DELETE RESTRICT,
 reviewer_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
 decision text NOT NULL CHECK(decision IN ('ACCEPTED','REJECTED','MORE_INFORMATION')),
 reason text NOT NULL, reviewed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX driver_document_reviews_doc ON driver_document_reviews(document_id,reviewed_at DESC);

CREATE TABLE driver_eligibility_reasons (
 id uuid PRIMARY KEY, driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 scope text NOT NULL, reason_code text NOT NULL, source_ref text,
 starts_at timestamptz NOT NULL DEFAULT now(), ends_at timestamptz,
 reviewed_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 CHECK(ends_at IS NULL OR ends_at>starts_at)
);
CREATE INDEX driver_eligibility_active ON driver_eligibility_reasons(driver_id,ends_at);

CREATE TABLE driver_schedules (
 id uuid PRIMARY KEY, driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 service_zone_id uuid REFERENCES service_zones(id) ON DELETE RESTRICT,
 recurrence_type text NOT NULL CHECK(recurrence_type IN ('ONE_OFF','RECURRING')),
 timezone text NOT NULL, recurrence_rule text,
 planned_start_at timestamptz, planned_end_at timestamptz,
 assigned_vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 assigned_charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 assignment_mode text NOT NULL CHECK(assignment_mode IN ('REQUIRED','SUGGESTED','NONE')),
 status text NOT NULL CHECK(status IN ('DRAFT','ACTIVE','CANCELLED','RETIRED')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0), UNIQUE(id,driver_id),
 CHECK((recurrence_type='RECURRING')=(recurrence_rule IS NOT NULL)),
 CHECK(planned_end_at IS NULL OR planned_start_at IS NULL OR planned_end_at>planned_start_at)
);
CREATE INDEX driver_schedules_driver ON driver_schedules(driver_id,status,created_at DESC);

CREATE TABLE driver_schedule_occurrences (
 id uuid PRIMARY KEY, schedule_id uuid NOT NULL REFERENCES driver_schedules(id) ON DELETE RESTRICT,
 occurrence_key text NOT NULL, schedule_version bigint NOT NULL CHECK(schedule_version>0),
 starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
 status text NOT NULL CHECK(status IN ('PLANNED','CANCELLED','EXCEPTION')),
 generated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(schedule_id,occurrence_key),
 CHECK(ends_at>starts_at)
);
CREATE INDEX driver_occurrences_time ON driver_schedule_occurrences(starts_at,ends_at);

CREATE TABLE driver_duty_sessions (
 id uuid PRIMARY KEY, driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 occurrence_id uuid REFERENCES driver_schedule_occurrences(id) ON DELETE RESTRICT,
 status text NOT NULL CHECK(status IN
 ('STARTING','AVAILABLE','OFFERED','ASSIGNED','EN_ROUTE','AT_CUSTOMER','SERVICING','BREAK','UNAVAILABLE','ENDING','ENDED','ABORTED')),
 started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz,
 expected_vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 expected_charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 actual_vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 actual_charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 source_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 state_version bigint NOT NULL DEFAULT 1 CHECK(state_version>0),
 UNIQUE(id,driver_id),
 CHECK(ended_at IS NULL OR ended_at>=started_at),
 CHECK((status IN ('ENDED','ABORTED'))=(ended_at IS NOT NULL))
);
CREATE UNIQUE INDEX driver_one_open_duty ON driver_duty_sessions(driver_id) WHERE ended_at IS NULL;

CREATE TABLE driver_availability_intervals (
 id uuid PRIMARY KEY, duty_id uuid NOT NULL REFERENCES driver_duty_sessions(id) ON DELETE RESTRICT,
 availability text NOT NULL CHECK(availability IN ('AVAILABLE','BREAK','UNAVAILABLE')),
 starts_at timestamptz NOT NULL, ends_at timestamptz,
 reason text, recorded_at timestamptz NOT NULL DEFAULT now(),
 CHECK(ends_at IS NULL OR ends_at>starts_at)
);
CREATE INDEX driver_availability_duty ON driver_availability_intervals(duty_id,starts_at DESC);

CREATE TABLE driver_location_samples (
 id uuid PRIMARY KEY, driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 duty_id uuid REFERENCES driver_duty_sessions(id) ON DELETE RESTRICT,
 device_sample_key text NOT NULL, latitude numeric(9,6) NOT NULL CHECK(latitude BETWEEN -90 AND 90),
 longitude numeric(9,6) NOT NULL CHECK(longitude BETWEEN -180 AND 180),
 accuracy_m numeric(10,2) CHECK(accuracy_m IS NULL OR accuracy_m>=0),
 observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
 trust_state text NOT NULL CHECK(trust_state IN ('ACCEPTED','STALE','REJECTED')),
 UNIQUE(driver_id,device_sample_key), UNIQUE(driver_id,id)
);
CREATE INDEX driver_location_latest ON driver_location_samples(driver_id,observed_at DESC,id);

CREATE TABLE driver_location_state (
 driver_id uuid PRIMARY KEY REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 last_sample_id uuid NOT NULL, latitude numeric(9,6) NOT NULL, longitude numeric(9,6) NOT NULL,
 accuracy_m numeric(10,2), observed_at timestamptz NOT NULL,
 projection_version bigint NOT NULL DEFAULT 1 CHECK(projection_version>0),
 FOREIGN KEY(driver_id,last_sample_id) REFERENCES driver_location_samples(driver_id,id) ON DELETE RESTRICT
);

CREATE TABLE charger_capability_projections (
 charger_id uuid PRIMARY KEY REFERENCES chargers(id) ON DELETE RESTRICT,
 hal_mapping_version bigint, capability_digest bytea,
 capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
 observed_at timestamptz, last_hal_sequence bigint,
 projection_version bigint NOT NULL DEFAULT 1 CHECK(projection_version>0)
);

CREATE TABLE driver_asset_assignments (
 id uuid PRIMARY KEY, driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 schedule_id uuid REFERENCES driver_schedules(id) ON DELETE RESTRICT,
 occurrence_id uuid REFERENCES driver_schedule_occurrences(id) ON DELETE RESTRICT,
 vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 starts_at timestamptz NOT NULL, ends_at timestamptz,
 status text NOT NULL CHECK(status IN ('PLANNED','CONFIRMED','ENDED','CANCELLED')),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(ends_at IS NULL OR ends_at>starts_at),
 FOREIGN KEY(schedule_id,driver_id) REFERENCES driver_schedules(id,driver_id) ON DELETE RESTRICT,
 CHECK(vehicle_id IS NOT NULL OR charger_id IS NOT NULL)
);
CREATE INDEX driver_asset_assignments_window ON driver_asset_assignments(driver_id,starts_at,ends_at);

CREATE TABLE asset_claims (
 id uuid PRIMARY KEY, duty_id uuid NOT NULL REFERENCES driver_duty_sessions(id) ON DELETE RESTRICT,
 driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 vehicle_id uuid NOT NULL REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 charger_id uuid NOT NULL REFERENCES chargers(id) ON DELETE RESTRICT,
 claim_token uuid NOT NULL UNIQUE, status text NOT NULL CHECK(status IN ('ACTIVE','RELEASED','INVALIDATED')),
 acquired_at timestamptz NOT NULL DEFAULT now(), released_at timestamptz,
 acquire_evidence jsonb NOT NULL DEFAULT '{}'::jsonb, release_reason text,
 CHECK((status='ACTIVE')=(released_at IS NULL)),
 FOREIGN KEY(duty_id,driver_id) REFERENCES driver_duty_sessions(id,driver_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX asset_claim_one_active_duty ON asset_claims(duty_id) WHERE status='ACTIVE';
CREATE UNIQUE INDEX asset_claim_one_active_driver ON asset_claims(driver_id) WHERE status='ACTIVE';
CREATE UNIQUE INDEX asset_claim_one_active_vehicle ON asset_claims(vehicle_id) WHERE status='ACTIVE';
CREATE UNIQUE INDEX asset_claim_one_active_charger ON asset_claims(charger_id) WHERE status='ACTIVE';

CREATE TABLE asset_mismatch_reports (
 id uuid PRIMARY KEY, duty_id uuid NOT NULL REFERENCES driver_duty_sessions(id) ON DELETE RESTRICT,
 expected_vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 observed_vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE RESTRICT,
 expected_charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 observed_charger_id uuid REFERENCES chargers(id) ON DELETE RESTRICT,
 evidence_object_key text, status text NOT NULL CHECK(status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
 reported_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz,
 resolved_by uuid REFERENCES accounts(id) ON DELETE SET NULL, resolution_reason text
);
CREATE INDEX asset_mismatch_duty ON asset_mismatch_reports(duty_id,reported_at DESC);

CREATE TABLE asset_lifecycle_events (
 id uuid PRIMARY KEY, asset_kind text NOT NULL CHECK(asset_kind IN ('VEHICLE','CHARGER','CONNECTOR')),
 asset_id uuid NOT NULL, action text NOT NULL,
 actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 evidence jsonb NOT NULL DEFAULT '{}'::jsonb, reason text NOT NULL,
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX asset_lifecycle_resource ON asset_lifecycle_events(asset_kind,asset_id,occurred_at DESC);
