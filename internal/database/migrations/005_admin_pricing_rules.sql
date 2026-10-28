-- Admin tariff revisions, publication topology and audit. Migration 004 is
-- reserved for the parallel User backend branch and must be merged first.
-- No hub identifier, guessed tax percentage, or live-job price is stored here.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version=4) THEN
  RAISE EXCEPTION 'Pricing migration 005 requires separately reviewed User migration 004 first';
 END IF;
END $$;
CREATE TABLE pricing_policies (
 id uuid PRIMARY KEY,
 policy_key text COLLATE "C" NOT NULL CHECK (policy_key ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 policy_version bigint NOT NULL CHECK (policy_version > 0),
 scope_kind text COLLATE "C" NOT NULL CHECK (scope_kind ~ '^[A-Z][A-Z0-9_]{0,63}$'),
 scope_key text COLLATE "C" NOT NULL CHECK (scope_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$'),
 starts_at timestamptz,
 ends_at timestamptz,
 currency text NOT NULL CHECK (currency = 'INR'),
 rules jsonb NOT NULL CHECK (jsonb_typeof(rules)='array' AND jsonb_array_length(rules) BETWEEN 1 AND 128),
 lifecycle text NOT NULL DEFAULT 'DRAFT' CHECK (lifecycle IN ('DRAFT','PUBLISHED','WITHDRAWN')),
 state_version bigint NOT NULL DEFAULT 1 CHECK (state_version > 0),
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
 created_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 published_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 withdrawn_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 published_at timestamptz,
 withdrawn_at timestamptz,
 UNIQUE (policy_key,policy_version),
 CHECK ((starts_at IS NOT NULL OR ends_at IS NULL) AND (ends_at IS NULL OR starts_at < ends_at)),
 CHECK ((lifecycle='DRAFT' AND published_at IS NULL AND withdrawn_at IS NULL)
   OR (lifecycle='PUBLISHED' AND published_at IS NOT NULL AND withdrawn_at IS NULL)
   OR (lifecycle='WITHDRAWN' AND published_at IS NOT NULL AND withdrawn_at IS NOT NULL))
);
-- Fast read of exact scope; the publication transaction validates arbitrary
-- strictly nested bounded windows while serializing that scope under a lock.
CREATE INDEX pricing_scope_publication ON pricing_policies(scope_kind,scope_key,lifecycle,starts_at,ends_at);
CREATE INDEX pricing_latest_revision ON pricing_policies(policy_key,policy_version DESC);
CREATE UNIQUE INDEX pricing_published_one_root ON pricing_policies(scope_kind,scope_key)
 WHERE lifecycle='PUBLISHED' AND starts_at IS NULL;
CREATE UNIQUE INDEX pricing_published_one_open_start ON pricing_policies(scope_kind,scope_key,starts_at)
 WHERE lifecycle='PUBLISHED' AND starts_at IS NOT NULL AND ends_at IS NULL;

CREATE TABLE pricing_policy_audit (
 id bigserial PRIMARY KEY,
 policy_id uuid NOT NULL REFERENCES pricing_policies(id) ON DELETE RESTRICT,
 actor_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
 action text NOT NULL CHECK (action IN ('DRAFT_CREATED','PUBLISHED','WITHDRAWN')),
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
 before_value jsonb,
 after_value jsonb NOT NULL,
 occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pricing_policy_audit_history ON pricing_policy_audit(policy_id,id DESC);
