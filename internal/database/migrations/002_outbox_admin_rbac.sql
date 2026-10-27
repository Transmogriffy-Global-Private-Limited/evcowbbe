-- Additive auth-email outbox and scoped Admin RBAC. Migration 001 is immutable.
CREATE TABLE auth_mail_outbox (
 id uuid PRIMARY KEY,
 account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 recipient_email text NOT NULL,
 template text NOT NULL CHECK (template IN ('verification','reset','otp','security')),
 payload_ciphertext bytea,
 payload_nonce bytea,
 credential_kind text CHECK (credential_kind IN ('VERIFY_EMAIL','RESET_PASSWORD','OTP')),
 credential_digest bytea,
 credential_challenge_id uuid,
 expires_at timestamptz,
 state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','LEASED','SENT','EXPIRED','DEAD')),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid,
 lease_until timestamptz,
 last_error_class text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 sent_at timestamptz,
 CHECK ((payload_ciphertext IS NULL) = (payload_nonce IS NULL)),
 CHECK ((credential_kind IS NULL AND credential_digest IS NULL AND credential_challenge_id IS NULL)
     OR (credential_kind IN ('VERIFY_EMAIL','RESET_PASSWORD') AND credential_digest IS NOT NULL AND credential_challenge_id IS NULL)
     OR (credential_kind='OTP' AND credential_digest IS NOT NULL AND credential_challenge_id IS NOT NULL))
);
CREATE INDEX auth_mail_outbox_due ON auth_mail_outbox(next_attempt_at, created_at)
 WHERE state IN ('PENDING','LEASED');
CREATE INDEX auth_mail_outbox_account ON auth_mail_outbox(account_id,created_at DESC);

CREATE TABLE admin_role_assignments (
 account_id uuid NOT NULL,
 plane text NOT NULL DEFAULT 'admin' CHECK (plane='admin'),
 role_key text NOT NULL CHECK (role_key IN ('FULL_ADMIN','OPERATIONS','DRIVER_RECRUITMENT','FINANCE_COMMERCIAL','SUPPORT','TECHNICAL_HAL','READ_ONLY')),
 assigned_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 assigned_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,role_key),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane)
);
CREATE TABLE admin_capability_overrides (
 account_id uuid NOT NULL,
 plane text NOT NULL DEFAULT 'admin' CHECK (plane='admin'),
 capability text NOT NULL,
 decision text NOT NULL CHECK (decision IN ('ALLOW','DENY')),
 changed_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 changed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,capability),
 FOREIGN KEY(account_id,plane) REFERENCES principals(account_id,plane)
);
CREATE TABLE admin_rbac_audit (
 id bigserial PRIMARY KEY,
 actor_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 target_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 operation text NOT NULL,
 subject text NOT NULL,
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX admin_rbac_audit_target ON admin_rbac_audit(target_id,occurred_at DESC);
