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
