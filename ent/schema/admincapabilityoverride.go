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

// AdminCapabilityOverride enforces explicit capability-level ALLOW/DENY.
type AdminCapabilityOverride struct{ ent.Schema }

func (AdminCapabilityOverride) Mixin() []ent.Mixin {
	return []ent.Mixin{sqlIntegrity("admin_capability_overrides")}
}

func (AdminCapabilityOverride) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "admin_capability_overrides"}}
}

func (AdminCapabilityOverride) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(), // migration 006 surrogate; natural key retained
		field.UUID("account_id", uuid.UUID{}).Immutable(),
		field.String("plane").Default("admin").Immutable(),
		field.String("capability").Immutable(),
		field.Enum("decision").Values("ALLOW", "DENY"),
		field.UUID("changed_by", uuid.UUID{}).Optional().Nillable(),
		field.Time("changed_at").Default(time.Now),
	}
}

func (AdminCapabilityOverride) Indexes() []ent.Index {
	return []ent.Index{index.Fields("account_id", "capability").Unique().StorageKey("admin_capability_overrides_account_id_capability_key")}
}
