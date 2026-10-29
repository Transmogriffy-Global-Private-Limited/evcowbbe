package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

type AdminRbacAudit struct{ ent.Schema }

func (AdminRbacAudit) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("admin_rbac_audit")} }

func (AdminRbacAudit) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "admin_rbac_audit"}}
}

func (AdminRbacAudit) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(),
		field.UUID("actor_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.UUID("target_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.String("operation").Immutable(),
		field.String("subject").Immutable(),
		field.String("reason").MinLen(8).MaxLen(500).Immutable(),
		field.Time("occurred_at").Default(time.Now).Immutable(),
	}
}
