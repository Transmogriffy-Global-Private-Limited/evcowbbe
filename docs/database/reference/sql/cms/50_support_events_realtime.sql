-- TARGET-STATE REFERENCE DDL ONLY. Requires CMS 001-005 plus jobs/dispatch.
-- Source domain events commit atomically with business state; all deliveries
-- are independently recoverable. Visibility must be enforced by service code.

CREATE TABLE idempotency_records (
 id uuid PRIMARY KEY, actor_scope text NOT NULL, actor_key text NOT NULL,
 operation_key text NOT NULL, request_key text NOT NULL,
 request_sha256 bytea NOT NULL CHECK(octet_length(request_sha256)=32),
 state text NOT NULL CHECK(state IN ('PROCESSING','COMPLETED','FAILED')),
 response_status integer, response_body jsonb, expires_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(actor_scope,actor_key,operation_key,request_key)
);
CREATE INDEX idempotency_records_expiry ON idempotency_records(expires_at);

CREATE TABLE domain_events (
 id uuid PRIMARY KEY, aggregate_kind text NOT NULL, aggregate_id uuid NOT NULL,
 aggregate_version bigint NOT NULL CHECK(aggregate_version>0),
 event_kind text NOT NULL, schema_version integer NOT NULL CHECK(schema_version>0),
 payload jsonb NOT NULL, correlation_id uuid, causation_id uuid,
 occurred_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(aggregate_kind,aggregate_id,aggregate_version)
);
CREATE INDEX domain_events_aggregate ON domain_events(aggregate_kind,aggregate_id,aggregate_version DESC);

CREATE TABLE outbox_deliveries (
 event_id uuid NOT NULL REFERENCES domain_events(id) ON DELETE RESTRICT,
 consumer_key text NOT NULL, state text NOT NULL CHECK(state IN ('PENDING','LEASED','ACKED','DEAD')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT now(), lease_token uuid, lease_until timestamptz,
 last_error_class text, acknowledged_at timestamptz,
 PRIMARY KEY(event_id,consumer_key)
);
CREATE INDEX outbox_deliveries_due ON outbox_deliveries(consumer_key,state,next_attempt_at);

CREATE TABLE audit_events (
 id bigserial PRIMARY KEY, source_domain text NOT NULL,
 source_event_id uuid, actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 actor_plane text CHECK(actor_plane IN ('app','driver','admin')),
 action text NOT NULL, resource_kind text NOT NULL, resource_id uuid,
 reason_code text, evidence_digest bytea,
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_resource ON audit_events(resource_kind,resource_id,id DESC);

CREATE TABLE support_tickets (
 id uuid PRIMARY KEY, public_reference text COLLATE "C" NOT NULL UNIQUE,
 job_id uuid REFERENCES service_jobs(id) ON DELETE RESTRICT,
 requester_account_id uuid NOT NULL, requester_plane text NOT NULL
 CHECK(requester_plane IN ('app','driver','admin')),
 assigned_admin_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 category text NOT NULL, priority text NOT NULL CHECK(priority IN ('LOW','NORMAL','HIGH','URGENT')),
 status text NOT NULL CHECK(status IN ('OPEN','IN_PROGRESS','AWAITING_REQUESTER','RESOLVED','CLOSED')),
 subject text NOT NULL, sla_policy_version text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 closed_at timestamptz, version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 FOREIGN KEY(requester_account_id,requester_plane)
  REFERENCES principals(account_id,plane) ON DELETE RESTRICT
);
CREATE INDEX support_tickets_owner ON support_tickets(requester_account_id,requester_plane,created_at DESC);
CREATE INDEX support_tickets_queue ON support_tickets(status,priority,created_at);

CREATE TABLE support_messages (
 id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES support_tickets(id) ON DELETE RESTRICT,
 author_account_id uuid NOT NULL, author_plane text NOT NULL CHECK(author_plane IN ('app','driver','admin')),
 visibility text NOT NULL CHECK(visibility IN ('REQUESTER_AND_STAFF','STAFF_ONLY')),
 message_body text NOT NULL, attachment_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(author_account_id,author_plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT
);
CREATE INDEX support_messages_ticket ON support_messages(ticket_id,created_at,id);

CREATE TABLE support_events (
 id uuid PRIMARY KEY, ticket_id uuid NOT NULL REFERENCES support_tickets(id) ON DELETE RESTRICT,
 event_seq bigint NOT NULL CHECK(event_seq>0), event_kind text NOT NULL,
 actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 event_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 occurred_at timestamptz NOT NULL DEFAULT now(), UNIQUE(ticket_id,event_seq)
);

CREATE TABLE incidents (
 id uuid PRIMARY KEY, job_id uuid REFERENCES service_jobs(id) ON DELETE RESTRICT,
 reported_by_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 category text NOT NULL, severity text NOT NULL CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
 status text NOT NULL CHECK(status IN ('OPEN','INVESTIGATING','MITIGATED','RESOLVED','CLOSED')),
 description text NOT NULL, assigned_admin_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 opened_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz,
 version bigint NOT NULL DEFAULT 1 CHECK(version>0)
);
CREATE INDEX incidents_operations ON incidents(status,severity,opened_at);

CREATE TABLE incident_events (
 id uuid PRIMARY KEY, incident_id uuid NOT NULL REFERENCES incidents(id) ON DELETE RESTRICT,
 event_seq bigint NOT NULL CHECK(event_seq>0), event_kind text NOT NULL,
 actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 occurred_at timestamptz NOT NULL DEFAULT now(), UNIQUE(incident_id,event_seq)
);
CREATE TABLE incident_evidence (
 id uuid PRIMARY KEY, incident_id uuid NOT NULL REFERENCES incidents(id) ON DELETE RESTRICT,
 evidence_kind text NOT NULL, object_storage_key text NOT NULL,
 content_sha256 bytea NOT NULL CHECK(octet_length(content_sha256)=32),
 captured_at timestamptz, uploaded_at timestamptz NOT NULL DEFAULT now(),
 visibility text NOT NULL CHECK(visibility IN ('RESTRICTED','REVIEWERS'))
);
CREATE INDEX incident_evidence_incident ON incident_evidence(incident_id,uploaded_at);

CREATE TABLE ratings_reviews (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 author_account_id uuid NOT NULL, author_plane text NOT NULL CHECK(author_plane IN ('app','driver')),
 stars integer NOT NULL CHECK(stars BETWEEN 1 AND 5),
 review_text text, moderation_state text NOT NULL
 CHECK(moderation_state IN ('PENDING','VISIBLE','HIDDEN','FLAGGED')),
 submitted_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(author_account_id,author_plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT,
 UNIQUE(job_id,author_account_id,author_plane)
);

CREATE TABLE notifications (
 id uuid PRIMARY KEY, source_domain_event_id uuid REFERENCES domain_events(id) ON DELETE RESTRICT,
 recipient_account_id uuid NOT NULL, recipient_plane text NOT NULL
 CHECK(recipient_plane IN ('app','driver','admin')),
 topic text NOT NULL, template_key text NOT NULL, template_version integer NOT NULL CHECK(template_version>0),
 payload jsonb NOT NULL, visibility text NOT NULL CHECK(visibility IN ('PRIVATE','STAFF')),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz,
 FOREIGN KEY(recipient_account_id,recipient_plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT
);
CREATE INDEX notifications_inbox ON notifications(recipient_account_id,recipient_plane,created_at DESC,id);

CREATE TABLE notification_deliveries (
 id uuid PRIMARY KEY, notification_id uuid NOT NULL REFERENCES notifications(id) ON DELETE RESTRICT,
 channel text NOT NULL CHECK(channel IN ('IN_APP','EMAIL','PUSH','SMS')),
 attempt_no integer NOT NULL CHECK(attempt_no>0),
 state text NOT NULL CHECK(state IN ('PENDING','LEASED','DELIVERED','FAILED','DEAD')),
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, lease_until timestamptz, provider_receipt text, error_class text,
 delivered_at timestamptz, UNIQUE(notification_id,channel,attempt_no)
);
CREATE INDEX notification_deliveries_due ON notification_deliveries(channel,state,next_attempt_at);

CREATE TABLE notification_preferences (
 account_id uuid NOT NULL, plane text NOT NULL CHECK(plane IN ('app','driver','admin')),
 channel text NOT NULL, topic text NOT NULL, enabled boolean NOT NULL,
 consent_revision text, updated_at timestamptz NOT NULL DEFAULT now(),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 PRIMARY KEY(account_id,plane,channel,topic),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT
);

CREATE TABLE device_registrations (
 id uuid PRIMARY KEY, account_id uuid NOT NULL, plane text NOT NULL
 CHECK(plane IN ('app','driver','admin')),
 provider text NOT NULL, token_digest bytea NOT NULL CHECK(octet_length(token_digest)=32),
 token_ciphertext bytea NOT NULL, token_nonce bytea NOT NULL,
 key_version integer NOT NULL CHECK(key_version>0),
 status text NOT NULL CHECK(status IN ('ACTIVE','REVOKED')),
 created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
 UNIQUE(provider,token_digest),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT
);

CREATE TABLE realtime_events (
 seq bigserial PRIMARY KEY, source_domain_event_id uuid REFERENCES domain_events(id) ON DELETE RESTRICT,
 event_kind text NOT NULL, resource_kind text NOT NULL, resource_id uuid,
 schema_version integer NOT NULL CHECK(schema_version>0), payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz
);
CREATE TABLE realtime_event_targets (
 event_seq bigint NOT NULL REFERENCES realtime_events(seq) ON DELETE RESTRICT,
 account_id uuid NOT NULL, plane text NOT NULL CHECK(plane IN ('app','driver','admin')),
 PRIMARY KEY(event_seq,account_id,plane),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT
);
CREATE INDEX realtime_targets_inbox ON realtime_event_targets(account_id,plane,event_seq DESC);

CREATE TABLE realtime_stream_tickets (
 digest bytea PRIMARY KEY CHECK(octet_length(digest)=32),
 account_id uuid NOT NULL, plane text NOT NULL CHECK(plane IN ('app','driver','admin')),
 scope_digest bytea NOT NULL CHECK(octet_length(scope_digest)=32),
 expires_at timestamptz NOT NULL, consumed_at timestamptz, revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT
);
CREATE INDEX realtime_ticket_expiry ON realtime_stream_tickets(expires_at);

CREATE TABLE async_exports (
 id uuid PRIMARY KEY, requester_account_id uuid NOT NULL, requester_plane text NOT NULL
 CHECK(requester_plane IN ('app','driver','admin')),
 export_kind text NOT NULL, scope_snapshot jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('QUEUED','RUNNING','READY','FAILED','EXPIRED')),
 object_storage_key text, content_sha256 bytea,
 expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 completed_at timestamptz,
 FOREIGN KEY(requester_account_id,requester_plane) REFERENCES principals(account_id,plane) ON DELETE RESTRICT,
 CHECK(content_sha256 IS NULL OR octet_length(content_sha256)=32)
);

CREATE TABLE worker_checkpoints (
 worker_kind text NOT NULL, partition_key text NOT NULL,
 cursor_text text, lease_token uuid, lease_until timestamptz,
 last_heartbeat_at timestamptz, version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 PRIMARY KEY(worker_kind,partition_key)
);

-- GATED: privacy/legal policy must specify lawful basis and retention; no
-- automatic deletion of job, billing, security or physical evidence.
CREATE TABLE legal_holds (
 id uuid PRIMARY KEY, subject_kind text NOT NULL, subject_id uuid NOT NULL,
 legal_basis_ref text NOT NULL, applied_by uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
 applied_at timestamptz NOT NULL DEFAULT now(), released_at timestamptz,
 release_reason text
);
CREATE INDEX legal_holds_subject ON legal_holds(subject_kind,subject_id,released_at);
CREATE TABLE data_deletion_ledger (
 id uuid PRIMARY KEY, subject_kind text NOT NULL, subject_id uuid NOT NULL,
 request_ref text NOT NULL UNIQUE, disposition text NOT NULL
 CHECK(disposition IN ('REQUESTED','BLOCKED','ANONYMIZED','REJECTED')),
 legal_basis_ref text NOT NULL, decided_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(), outcome_digest bytea
);
