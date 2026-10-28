-- TARGET-STATE REFERENCE DDL ONLY. Requires pricing 005, quotes/jobs and
-- physical sessions. Actual rates, taxation and provider remain decision gates.
-- Paise are bigint, fractional quantities/rates are NUMERIC (never binary float).

CREATE TABLE pricing_snapshots (
 id uuid PRIMARY KEY, quote_id uuid UNIQUE REFERENCES quotes(id) ON DELETE RESTRICT,
 job_id uuid UNIQUE REFERENCES service_jobs(id) ON DELETE RESTRICT,
 pricing_policy_id uuid NOT NULL REFERENCES pricing_policies(id) ON DELETE RESTRICT,
 policy_version bigint NOT NULL CHECK(policy_version>0),
 scope_kind text NOT NULL, scope_key text NOT NULL, currency text NOT NULL CHECK(currency='INR'),
 policy_rules_sha256 bytea NOT NULL CHECK(octet_length(policy_rules_sha256)=32),
 applied_rules jsonb NOT NULL, schema_version integer NOT NULL CHECK(schema_version>0),
 captured_at timestamptz NOT NULL DEFAULT now(),
 CHECK((quote_id IS NOT NULL) <> (job_id IS NOT NULL))
);

CREATE TABLE pricing_evaluations (
 id uuid PRIMARY KEY, quote_id uuid REFERENCES quotes(id) ON DELETE RESTRICT,
 job_id uuid REFERENCES service_jobs(id) ON DELETE RESTRICT,
 snapshot_id uuid NOT NULL REFERENCES pricing_snapshots(id) ON DELETE RESTRICT,
 evaluation_kind text NOT NULL CHECK(evaluation_kind IN ('ESTIMATE','FINAL','CORRECTION')),
 revision bigint NOT NULL CHECK(revision>0),
 input_sha256 bytea NOT NULL CHECK(octet_length(input_sha256)=32),
 inputs jsonb NOT NULL, evaluator_version text NOT NULL,
 subtotal_paise bigint NOT NULL, tax_paise bigint NOT NULL,
 total_paise bigint NOT NULL, currency text NOT NULL CHECK(currency='INR'),
 state text NOT NULL CHECK(state IN ('PRELIMINARY','FINALIZED','SUPERSEDED')),
 finalized_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((quote_id IS NOT NULL) <> (job_id IS NOT NULL)),
 CHECK(total_paise=subtotal_paise+tax_paise),
 CHECK((state='PRELIMINARY' AND finalized_at IS NULL)
  OR (state IN ('FINALIZED','SUPERSEDED') AND finalized_at IS NOT NULL))
);
CREATE UNIQUE INDEX pricing_eval_quote_revision ON pricing_evaluations(quote_id,evaluation_kind,revision) WHERE quote_id IS NOT NULL;
CREATE UNIQUE INDEX pricing_eval_job_revision ON pricing_evaluations(job_id,evaluation_kind,revision) WHERE job_id IS NOT NULL;

CREATE TABLE pricing_line_items (
 id uuid PRIMARY KEY, evaluation_id uuid NOT NULL REFERENCES pricing_evaluations(id) ON DELETE RESTRICT,
 line_no integer NOT NULL CHECK(line_no>0), component_code text NOT NULL,
 description text NOT NULL, quantity numeric(20,6) NOT NULL,
 unit_code text NOT NULL, rate_minor_per_unit numeric(24,8) NOT NULL,
 amount_paise bigint NOT NULL, calculation_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 UNIQUE(evaluation_id,line_no)
);

CREATE TABLE commercial_adjustments (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 baseline_evaluation_id uuid NOT NULL REFERENCES pricing_evaluations(id) ON DELETE RESTRICT,
 adjustment_kind text NOT NULL, amount_paise bigint NOT NULL,
 reason text NOT NULL, approved_by uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
 evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now()
);

-- GATED: populate only after Finance/legal approves tax classes and effective dates.
CREATE TABLE tax_configurations (
 id uuid PRIMARY KEY, jurisdiction_key text NOT NULL, tax_code text NOT NULL,
 revision bigint NOT NULL CHECK(revision>0),
 tax_rate_fraction numeric(12,9) NOT NULL CHECK(tax_rate_fraction BETWEEN 0 AND 1),
 effective_from timestamptz NOT NULL, effective_to timestamptz,
 legal_reference text NOT NULL, approved_by uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
 approved_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(jurisdiction_key,tax_code,revision),
 CHECK(effective_to IS NULL OR effective_to>effective_from)
);
CREATE TABLE tax_snapshots (
 id uuid PRIMARY KEY, evaluation_id uuid NOT NULL REFERENCES pricing_evaluations(id) ON DELETE RESTRICT,
 configuration_id uuid REFERENCES tax_configurations(id) ON DELETE RESTRICT,
 jurisdiction_key text NOT NULL, tax_code text NOT NULL,
 taxable_base_paise bigint NOT NULL CHECK(taxable_base_paise>=0),
 applied_rate_fraction numeric(12,9) NOT NULL CHECK(applied_rate_fraction BETWEEN 0 AND 1),
 amount_paise bigint NOT NULL, calculation_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE payments (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 provider text NOT NULL, provider_payment_id text,
 amount_paise bigint NOT NULL CHECK(amount_paise>=0), currency text NOT NULL CHECK(currency='INR'),
 state text NOT NULL CHECK(state IN ('CREATED','AUTHORIZED','CAPTURED','FAILED','CANCELLED','PARTIALLY_REFUNDED','REFUNDED')),
 authorized_paise bigint NOT NULL DEFAULT 0 CHECK(authorized_paise>=0),
 captured_paise bigint NOT NULL DEFAULT 0 CHECK(captured_paise>=0),
 refunded_paise bigint NOT NULL DEFAULT 0 CHECK(refunded_paise>=0),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(refunded_paise<=captured_paise), CHECK(captured_paise<=amount_paise),
 CHECK(authorized_paise<=amount_paise)
);
CREATE UNIQUE INDEX payments_provider_identity ON payments(provider,provider_payment_id)
 WHERE provider_payment_id IS NOT NULL;
CREATE INDEX payments_job ON payments(job_id,created_at DESC);

CREATE TABLE payment_provider_events (
 id uuid PRIMARY KEY, provider text NOT NULL, provider_event_id text NOT NULL,
 payment_id uuid REFERENCES payments(id) ON DELETE RESTRICT,
 event_type text NOT NULL, payload_sha256 bytea NOT NULL CHECK(octet_length(payload_sha256)=32),
 signature_verified boolean NOT NULL,
 provider_occurred_at timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
 processed_at timestamptz, processing_error_class text,
 UNIQUE(provider,provider_event_id)
);
CREATE INDEX payment_provider_unprocessed ON payment_provider_events(received_at,id) WHERE processed_at IS NULL;

CREATE TABLE refunds (
 id uuid PRIMARY KEY, payment_id uuid NOT NULL REFERENCES payments(id) ON DELETE RESTRICT,
 provider text NOT NULL, provider_refund_id text,
 amount_paise bigint NOT NULL CHECK(amount_paise>0), currency text NOT NULL CHECK(currency='INR'),
 state text NOT NULL CHECK(state IN ('REQUESTED','PENDING','SUCCEEDED','FAILED','CANCELLED')),
 reason text NOT NULL, requested_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE UNIQUE INDEX refunds_provider_identity ON refunds(provider,provider_refund_id) WHERE provider_refund_id IS NOT NULL;
CREATE INDEX refunds_payment ON refunds(payment_id,created_at DESC);
-- Refund-sum cap across rows is a locked payment transaction, not a row CHECK.

CREATE TABLE invoices (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 invoice_number text COLLATE "C" UNIQUE,
 invoice_kind text NOT NULL CHECK(invoice_kind IN ('INVOICE','CREDIT_NOTE')),
 corrects_invoice_id uuid REFERENCES invoices(id) ON DELETE RESTRICT,
 evaluation_id uuid NOT NULL REFERENCES pricing_evaluations(id) ON DELETE RESTRICT,
 billing_snapshot jsonb NOT NULL, subtotal_paise bigint NOT NULL,
 tax_paise bigint NOT NULL, total_paise bigint NOT NULL,
 currency text NOT NULL CHECK(currency='INR'),
 state text NOT NULL CHECK(state IN ('DRAFT','ISSUED','VOID')),
 issued_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(total_paise=subtotal_paise+tax_paise),
 CHECK(state='DRAFT' OR (invoice_number IS NOT NULL AND issued_at IS NOT NULL))
);
CREATE INDEX invoices_job ON invoices(job_id,created_at DESC);
CREATE TABLE invoice_artifacts (
 id uuid PRIMARY KEY, invoice_id uuid NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
 format text NOT NULL CHECK(format IN ('PDF','JSON')),
 artifact_version integer NOT NULL CHECK(artifact_version>0),
 object_storage_key text NOT NULL,
 content_sha256 bytea NOT NULL CHECK(octet_length(content_sha256)=32),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(invoice_id,format,artifact_version)
);

CREATE TABLE compensation_policies (
 id uuid PRIMARY KEY, policy_key text NOT NULL, policy_version bigint NOT NULL CHECK(policy_version>0),
 lifecycle text NOT NULL CHECK(lifecycle IN ('DRAFT','PUBLISHED','WITHDRAWN')),
 effective_from timestamptz, effective_to timestamptz,
 approved_by uuid REFERENCES accounts(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
 UNIQUE(policy_key,policy_version), CHECK(effective_to IS NULL OR effective_from IS NULL OR effective_to>effective_from)
);
CREATE TABLE compensation_rules (
 id uuid PRIMARY KEY, policy_id uuid NOT NULL REFERENCES compensation_policies(id) ON DELETE RESTRICT,
 rule_order integer NOT NULL CHECK(rule_order>0), rule_kind text NOT NULL,
 rule_schema_version integer NOT NULL CHECK(rule_schema_version>0),
 rule_config jsonb NOT NULL, UNIQUE(policy_id,rule_order)
);
CREATE TABLE compensation_calculations (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES service_jobs(id) ON DELETE RESTRICT,
 driver_id uuid NOT NULL REFERENCES driver_profiles(id) ON DELETE RESTRICT,
 policy_id uuid NOT NULL REFERENCES compensation_policies(id) ON DELETE RESTRICT,
 revision bigint NOT NULL CHECK(revision>0),
 input_sha256 bytea NOT NULL CHECK(octet_length(input_sha256)=32), input_snapshot jsonb NOT NULL,
 total_paise bigint NOT NULL, currency text NOT NULL CHECK(currency='INR'),
 state text NOT NULL CHECK(state IN ('PRELIMINARY','FINALIZED','SUPERSEDED')),
 created_at timestamptz NOT NULL DEFAULT now(), finalized_at timestamptz,
 UNIQUE(job_id,driver_id,revision)
);
CREATE TABLE compensation_line_items (
 id uuid PRIMARY KEY, calculation_id uuid NOT NULL REFERENCES compensation_calculations(id) ON DELETE RESTRICT,
 line_no integer NOT NULL CHECK(line_no>0), component_code text NOT NULL,
 quantity numeric(20,6) NOT NULL, unit text NOT NULL,
 rate_minor_per_unit numeric(24,8) NOT NULL, amount_paise bigint NOT NULL,
 UNIQUE(calculation_id,line_no)
);
CREATE TABLE compensation_adjustments (
 id uuid PRIMARY KEY, calculation_id uuid NOT NULL REFERENCES compensation_calculations(id) ON DELETE RESTRICT,
 amount_paise bigint NOT NULL, reason text NOT NULL,
 approved_by uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
 occurred_at timestamptz NOT NULL DEFAULT now()
);
