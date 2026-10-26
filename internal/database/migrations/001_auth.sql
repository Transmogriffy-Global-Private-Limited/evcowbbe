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
