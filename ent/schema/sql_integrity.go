package schema

import (
	"fmt"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/index"
	"entgo.io/ent/schema/mixin"
)

// historicalSQLIntegrity is part of the Ent model, not a second schema
// definition. It records PostgreSQL invariants that cannot be expressed by
// Ent's regular field validators. Never weaken an invariant merely to obtain
// an empty Atlas diff: the old SQL (001-006) remains immutable evidence.
//
// SQL CHECKs are deliberately declared here rather than sourced from migration
// files during generation, so future migrations have one editable source:
// ent/schema. The parity test checks these against immutable SQL independently.
type historicalSQLIntegrity struct {
	mixin.Schema
	table string
}

func sqlIntegrity(table string) ent.Mixin { return historicalSQLIntegrity{table: table} }

func (m historicalSQLIntegrity) Annotations() []schema.Annotation {
	checks := sqlChecks[m.table]
	if len(checks) == 0 {
		return nil
	}
	named := make(map[string]string, len(checks))
	for i, check := range checks {
		named[fmt.Sprintf("%s_ent_check_%02d", m.table, i+1)] = check
	}
	return []schema.Annotation{entsql.Checks(named)}
}

func (m historicalSQLIntegrity) Indexes() []ent.Index { return sqlIndexes[m.table] }

func (m historicalSQLIntegrity) Edges() []ent.Edge { return sqlEdges[m.table] }

// Explicit FK edges make Ent queries discoverable for Prisma-experienced
// developers. A column-only declaration would expose no typed relation.
// The historical multi-column (account_id, plane) principal references remain
// guarded by PostgreSQL constraints and an independent invariant test: Ent
// cannot model a composite target FK as one ordinary edge.
func accountFK(name, column string, required, immutable bool, deletion entsql.ReferenceOption) ent.Edge {
	b := edge.To(name, Account.Type).Unique().Field(column).Annotations(entsql.OnDelete(deletion))
	if required {
		b.Required()
	}
	if immutable {
		b.Immutable()
	}
	return b
}

func requiredChargerFK(name, column string) ent.Edge {
	return edge.To(name, Charger.Type).Unique().Required().Immutable().Field(column).
		Annotations(entsql.OnDelete(entsql.Restrict))
}

func requiredVehicleFK(name, column string) ent.Edge {
	return edge.To(name, ServiceVehicle.Type).Unique().Required().Immutable().Field(column).
		Annotations(entsql.OnDelete(entsql.Restrict))
}

var sqlEdges = map[string][]ent.Edge{
	"principals": {
		edge.To("account", Account.Type).Unique().Required().Immutable().Field("account_id").
			Annotations(entsql.OnDelete(entsql.Cascade)),
	},
	"auth_sessions": {accountFK("account", "account_id", true, true, entsql.Cascade)},
	"consumed_refresh_tokens": {
		edge.To("session", AuthSession.Type).Unique().Required().Immutable().Field("session_id").
			Annotations(entsql.OnDelete(entsql.Cascade)),
	},
	"auth_challenges":    {accountFK("account", "account_id", true, true, entsql.Cascade)},
	"auth_action_tokens": {accountFK("account", "account_id", true, true, entsql.Cascade)},
	"security_audit":     {accountFK("account", "account_id", false, true, entsql.SetNull)},
	"auth_mail_outbox":   {accountFK("account", "account_id", false, true, entsql.SetNull)},
	"admin_role_assignments": {
		accountFK("assigned_by_account", "assigned_by", false, true, entsql.SetNull),
	},
	"admin_capability_overrides": {
		accountFK("changed_by_account", "changed_by", false, false, entsql.SetNull),
	},
	"admin_rbac_audit": {
		accountFK("actor", "actor_id", false, true, entsql.SetNull),
		accountFK("target", "target_id", false, true, entsql.SetNull),
	},
	"service_vehicles": {
		accountFK("creator", "created_by", false, false, entsql.SetNull),
		accountFK("updater", "updated_by", false, false, entsql.SetNull),
	},
	"chargers": {
		accountFK("creator", "created_by", false, false, entsql.SetNull),
		accountFK("updater", "updated_by", false, false, entsql.SetNull),
	},
	"charger_connectors": {
		requiredChargerFK("charger", "charger_id"),
		accountFK("creator", "created_by", false, false, entsql.SetNull),
		accountFK("updater", "updated_by", false, false, entsql.SetNull),
	},
	"vehicle_charger_pairings": {
		requiredVehicleFK("vehicle", "vehicle_id"),
		requiredChargerFK("charger", "charger_id"),
		accountFK("creator", "created_by", false, true, entsql.SetNull),
		accountFK("ender", "ended_by", false, false, entsql.SetNull),
	},
	"asset_maintenance_periods": {
		edge.To("vehicle", ServiceVehicle.Type).Unique().Field("vehicle_id").Immutable().
			Annotations(entsql.OnDelete(entsql.Restrict)),
		edge.To("charger", Charger.Type).Unique().Field("charger_id").Immutable().
			Annotations(entsql.OnDelete(entsql.Restrict)),
		accountFK("creator", "created_by", false, true, entsql.SetNull),
	},
	"fleet_asset_audit": {
		accountFK("actor", "actor_id", false, true, entsql.SetNull),
	},
	"mutation_idempotency_receipts": {
		accountFK("actor", "actor_id", true, true, entsql.Cascade),
	},
	"customer_master_audit": {
		accountFK("actor_account", "actor_account_id", false, true, entsql.SetNull),
	},
	"pricing_policies": {
		accountFK("creator", "created_by", false, true, entsql.SetNull),
		accountFK("publisher", "published_by", false, false, entsql.SetNull),
		accountFK("withdrawer", "withdrawn_by", false, false, entsql.SetNull),
	},
	"pricing_policy_audit": {accountFK("actor", "actor_id", false, true, entsql.SetNull)},
}

// Checks exactly as enforced by 001-005, with no policy-level shortcuts.
// Newly proposed 006 changes keys but adds no CHECK constraints.
var sqlChecks = map[string][]string{
	"accounts": {
		"email = lower(email)",
		"length(email) BETWEEN 3 AND 254",
	},
	"principals": {
		"plane IN ('app','driver','admin')",
		"status IN ('ACTIVE','PENDING','SUSPENDED')",
	},
	"auth_sessions": {"plane IN ('app','driver','admin')"},
	"auth_challenges": {
		"plane IN ('app','driver','admin')",
		"purpose IN ('LOGIN_2FA','ENABLE_2FA','DISABLE_2FA')",
		"attempts>=0",
	},
	"auth_action_tokens": {"kind IN ('VERIFY_EMAIL','RESET_PASSWORD')"},
	"auth_rate_limits":   {"hits>=0"},
	"auth_mail_outbox": {
		"template IN ('verification','reset','otp','security')",
		"credential_kind IN ('VERIFY_EMAIL','RESET_PASSWORD','OTP')",
		"state IN ('PENDING','LEASED','SENT','EXPIRED','DEAD')",
		"attempts>=0",
		"(payload_ciphertext IS NULL) = (payload_nonce IS NULL)",
		"(credential_kind IS NULL AND credential_digest IS NULL AND credential_challenge_id IS NULL) OR (credential_kind IN ('VERIFY_EMAIL','RESET_PASSWORD') AND credential_digest IS NOT NULL AND credential_challenge_id IS NULL) OR (credential_kind='OTP' AND credential_digest IS NOT NULL AND credential_challenge_id IS NOT NULL)",
	},
	"admin_role_assignments": {
		"plane='admin'",
		"role_key IN ('FULL_ADMIN','OPERATIONS','DRIVER_RECRUITMENT','FINANCE_COMMERCIAL','SUPPORT','TECHNICAL_HAL','READ_ONLY')",
	},
	"admin_capability_overrides": {"plane='admin'", "decision IN ('ALLOW','DENY')"},
	"admin_rbac_audit":           {"length(btrim(reason)) BETWEEN 8 AND 500"},
	"service_vehicles": {
		"length(asset_tag) BETWEEN 2 AND 64",
		"registration_number IS NULL OR length(registration_number) BETWEEN 2 AND 32",
		"length(make) BETWEEN 1 AND 120",
		"length(model) BETWEEN 1 AND 120",
		"inspection_status IN ('UNKNOWN','VALID','FAILED','EXPIRED')",
		"operational_status IN ('AVAILABLE','OUT_OF_SERVICE','RETIRED')",
		"maintenance_status IN ('UNKNOWN','CLEAR','SCHEDULED','ACTIVE')",
		"notes IS NULL OR length(notes) <= 4000",
		"version > 0",
		"operational_status <> 'RETIRED' OR is_active = false",
	},
	"chargers": {
		"length(asset_tag) BETWEEN 2 AND 64",
		"serial_number IS NULL OR length(serial_number) BETWEEN 2 AND 120",
		"length(manufacturer) BETWEEN 1 AND 120",
		"length(model) BETWEEN 1 AND 120",
		"rated_power_w IS NULL OR rated_power_w > 0",
		"operational_status IN ('AVAILABLE','OUT_OF_SERVICE','RETIRED')",
		"maintenance_status IN ('UNKNOWN','CLEAR','SCHEDULED','ACTIVE')",
		"notes IS NULL OR length(notes) <= 4000",
		"version > 0",
		"operational_status <> 'RETIRED' OR is_active = false",
	},
	"charger_connectors": {
		"connector_number > 0",
		"length(connector_type) BETWEEN 2 AND 64",
		"max_power_w > 0",
		"version > 0",
		"supports_ac OR supports_dc",
	},
	"vehicle_charger_pairings": {
		"status IN ('ACTIVE','ENDED','INVALIDATED')",
		"source IN ('ADMIN','RECONCILIATION')",
		"length(btrim(reason)) BETWEEN 8 AND 500",
		"end_reason IS NULL OR length(btrim(end_reason)) BETWEEN 8 AND 500",
		"effective_to IS NULL OR effective_to > effective_from",
		"(status = 'ACTIVE' AND effective_to IS NULL) OR (status <> 'ACTIVE' AND effective_to IS NOT NULL)",
	},
	"asset_maintenance_periods": {
		"length(btrim(reason)) BETWEEN 8 AND 500",
		"(vehicle_id IS NOT NULL) <> (charger_id IS NOT NULL)",
		"ends_at > starts_at",
	},
	"fleet_asset_audit": {"asset_kind IN ('VEHICLE','CHARGER','CONNECTOR','PAIRING','MAINTENANCE')"},
	"mutation_idempotency_receipts": {
		"length(request_key) BETWEEN 16 AND 128",
		"octet_length(request_digest)=32",
	},
	"customer_profiles": {
		"plane='app'",
		"full_name IS NULL OR length(btrim(full_name)) BETWEEN 1 AND 160",
		"primary_phone IS NULL OR length(primary_phone) BETWEEN 7 AND 32",
		"status IN ('INCOMPLETE','ACTIVE','SUSPENDED','CLOSED')",
		"version > 0",
		"status <> 'ACTIVE' OR full_name IS NOT NULL",
	},
	"customer_vehicles": {
		"label IS NULL OR length(btrim(label)) BETWEEN 1 AND 100",
		"length(btrim(make)) BETWEEN 1 AND 120",
		"length(btrim(model)) BETWEEN 1 AND 120",
		"registration_number IS NULL OR (length(registration_number) BETWEEN 2 AND 32 AND registration_number=upper(registration_number))",
		"battery_capacity_wh IS NULL OR battery_capacity_wh > 0",
		"max_ac_power_w IS NULL OR max_ac_power_w > 0",
		"max_dc_power_w IS NULL OR max_dc_power_w > 0",
		"version > 0",
		"supports_ac IS DISTINCT FROM false OR max_ac_power_w IS NULL",
		"supports_dc IS DISTINCT FROM false OR max_dc_power_w IS NULL",
	},
	"customer_addresses": {
		"label IS NULL OR length(btrim(label)) BETWEEN 1 AND 80",
		"length(btrim(line1)) BETWEEN 1 AND 240",
		"length(btrim(city)) BETWEEN 1 AND 120",
		"length(btrim(state)) BETWEEN 1 AND 120",
		"postal_code IS NULL OR length(btrim(postal_code)) BETWEEN 3 AND 16",
		"country_code ~ '^[A-Z]{2}$'",
		"latitude BETWEEN -90 AND 90",
		"longitude BETWEEN -180 AND 180",
		"version > 0",
		"(latitude IS NULL) = (longitude IS NULL)",
		"NOT is_default OR is_active",
	},
	"customer_master_audit": {
		"resource_kind IN ('PROFILE','VEHICLE','ADDRESS')",
		"resulting_version > 0",
	},
	"pricing_policies": {
		"policy_key ~ '^[A-Z][A-Z0-9_]{0,63}$'",
		"policy_version > 0",
		"scope_kind ~ '^[A-Z][A-Z0-9_]{0,63}$'",
		"scope_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$'",
		"currency = 'INR'",
		"jsonb_typeof(rules)='array' AND jsonb_array_length(rules) BETWEEN 1 AND 128",
		"lifecycle IN ('DRAFT','PUBLISHED','WITHDRAWN')",
		"state_version > 0",
		"length(btrim(reason)) BETWEEN 8 AND 500",
		"(starts_at IS NOT NULL OR ends_at IS NULL) AND (ends_at IS NULL OR starts_at < ends_at)",
		"(lifecycle='DRAFT' AND published_at IS NULL AND withdrawn_at IS NULL) OR (lifecycle='PUBLISHED' AND published_at IS NOT NULL AND withdrawn_at IS NULL) OR (lifecycle='WITHDRAWN' AND published_at IS NOT NULL AND withdrawn_at IS NOT NULL)",
	},
	"pricing_policy_audit": {
		"action IN ('DRAFT_CREATED','PUBLISHED','WITHDRAWN')",
		"length(btrim(reason)) BETWEEN 8 AND 500",
	},
}

// Every user-defined index from 001-005 that is not already represented by a
// UNIQUE field or an existing Ent Indexes() method. StorageKey retains the
// historical operational name. The predicates are exact PostgreSQL policy.
var sqlIndexes = map[string][]ent.Index{
	"auth_sessions": {
		index.Fields("account_id").StorageKey("sessions_account").Annotations(entsql.IndexWhere("revoked_at IS NULL")),
		index.Fields("absolute_expires_at").StorageKey("sessions_expiry"),
	},
	"consumed_refresh_tokens": {index.Fields("consumed_at").StorageKey("consumed_refresh_expiry")},
	"auth_challenges":         {index.Fields("account_id", "purpose", "created_at").StorageKey("challenges_account").Annotations(entsql.DescColumns("created_at"))},
	"auth_action_tokens":      {index.Fields("account_id", "kind").StorageKey("action_account_kind")},
	"security_audit":          {index.Fields("account_id", "occurred_at").StorageKey("security_audit_account").Annotations(entsql.DescColumns("occurred_at"))},
	"auth_mail_outbox": {
		index.Fields("next_attempt_at", "created_at").StorageKey("auth_mail_outbox_due").Annotations(entsql.IndexWhere("state IN ('PENDING','LEASED')")),
		index.Fields("account_id", "created_at").StorageKey("auth_mail_outbox_account").Annotations(entsql.DescColumns("created_at")),
	},
	"admin_rbac_audit":   {index.Fields("target_id", "occurred_at").StorageKey("admin_rbac_audit_target").Annotations(entsql.DescColumns("occurred_at"))},
	"service_vehicles":   {index.Fields("operational_status", "created_at", "id").StorageKey("service_vehicles_search")},
	"chargers":           {index.Fields("operational_status", "created_at", "id").StorageKey("chargers_search")},
	"charger_connectors": {index.Fields("charger_id", "connector_number").StorageKey("charger_connectors_charger")},
	"vehicle_charger_pairings": {
		index.Fields("charger_id").Unique().StorageKey("vehicle_charger_one_active").Annotations(entsql.IndexWhere("status='ACTIVE'")),
		index.Fields("vehicle_id", "effective_from").StorageKey("vehicle_charger_history").Annotations(entsql.DescColumns("effective_from")),
	},
	"asset_maintenance_periods": {
		index.Fields("vehicle_id", "starts_at", "ends_at").StorageKey("vehicle_maintenance_window").Annotations(entsql.IndexWhere("vehicle_id IS NOT NULL")),
		index.Fields("charger_id", "starts_at", "ends_at").StorageKey("charger_maintenance_window").Annotations(entsql.IndexWhere("charger_id IS NOT NULL")),
	},
	"fleet_asset_audit":             {index.Fields("asset_kind", "asset_id", "id").StorageKey("fleet_asset_audit_resource").Annotations(entsql.DescColumns("id"))},
	"mutation_idempotency_receipts": {index.Fields("created_at").StorageKey("mutation_receipts_age")},
	"customer_profiles":             {index.Fields("status", "created_at", "id").StorageKey("customer_profiles_status")},
	"customer_vehicles": {
		index.Fields("id", "customer_id").Unique().StorageKey("customer_vehicles_id_customer_id_key"),
		index.Fields("customer_id", "registration_number").Unique().StorageKey("customer_vehicles_active_registration").Annotations(entsql.IndexWhere("is_active AND registration_number IS NOT NULL")),
		index.Fields("customer_id", "is_active", "created_at", "id").StorageKey("customer_vehicles_list").Annotations(entsql.DescColumns("created_at")),
	},
	"customer_addresses": {
		index.Fields("id", "customer_id").Unique().StorageKey("customer_addresses_id_customer_id_key"),
		index.Fields("customer_id").Unique().StorageKey("customer_addresses_one_active_default").Annotations(entsql.IndexWhere("is_active AND is_default")),
		index.Fields("customer_id", "is_active", "created_at", "id").StorageKey("customer_addresses_list").Annotations(entsql.DescColumns("created_at")),
	},
	"customer_master_audit": {
		index.Fields("customer_id", "id").StorageKey("customer_master_audit_customer").Annotations(entsql.DescColumns("id")),
		index.Fields("resource_kind", "resource_id", "id").StorageKey("customer_master_audit_resource").Annotations(entsql.DescColumns("id")),
	},
	"pricing_policies": {
		index.Fields("policy_key", "policy_version").Unique().StorageKey("pricing_policies_policy_key_policy_version_key"),
		index.Fields("scope_kind", "scope_key", "lifecycle", "starts_at", "ends_at").StorageKey("pricing_scope_publication"),
		index.Fields("policy_key", "policy_version").StorageKey("pricing_latest_revision").Annotations(entsql.DescColumns("policy_version")),
		index.Fields("scope_kind", "scope_key").Unique().StorageKey("pricing_published_one_root").Annotations(entsql.IndexWhere("lifecycle='PUBLISHED' AND starts_at IS NULL")),
		index.Fields("scope_kind", "scope_key", "starts_at").Unique().StorageKey("pricing_published_one_open_start").Annotations(entsql.IndexWhere("lifecycle='PUBLISHED' AND starts_at IS NOT NULL AND ends_at IS NULL")),
	},
	"pricing_policy_audit": {index.Fields("policy_id", "id").StorageKey("pricing_policy_audit_history").Annotations(entsql.DescColumns("id"))},
}
