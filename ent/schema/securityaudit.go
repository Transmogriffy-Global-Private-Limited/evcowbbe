package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// SecurityAudit is append-only authentication evidence.
type SecurityAudit struct{ ent.Schema }

func (SecurityAudit) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("security_audit")} }

func (SecurityAudit) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "security_audit"}}
}

func (SecurityAudit) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(),
		field.UUID("account_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.String("event").Immutable(),
		field.String("plane").Optional().Nillable().Immutable(),
		field.Time("occurred_at").Default(time.Now).Immutable(),
	}
}
