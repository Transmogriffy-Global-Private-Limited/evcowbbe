package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
	"github.com/google/uuid"
)

// AdminRoleAssignment requires an admin-plane principal, not merely an account.
type AdminRoleAssignment struct{ ent.Schema }

func (AdminRoleAssignment) Mixin() []ent.Mixin {
	return []ent.Mixin{sqlIntegrity("admin_role_assignments")}
}

func (AdminRoleAssignment) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "admin_role_assignments"}}
}

func (AdminRoleAssignment) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(), // migration 006 surrogate; natural key retained
		field.UUID("account_id", uuid.UUID{}).Immutable(),
		field.String("plane").Default("admin").Immutable(),
		field.Enum("role_key").Values("FULL_ADMIN", "OPERATIONS", "DRIVER_RECRUITMENT", "FINANCE_COMMERCIAL", "SUPPORT", "TECHNICAL_HAL", "READ_ONLY").Immutable(),
		field.UUID("assigned_by", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.Time("assigned_at").Default(time.Now).Immutable(),
	}
}

func (AdminRoleAssignment) Indexes() []ent.Index {
	return []ent.Index{index.Fields("account_id", "role_key").Unique().StorageKey("admin_role_assignments_account_id_role_key_key")}
}
