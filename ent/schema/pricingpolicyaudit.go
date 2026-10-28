package schema

import (
	"encoding/json"
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// PricingPolicyAudit is append-only evidence created by Admin policy workflows.
type PricingPolicyAudit struct{ ent.Schema }

func (PricingPolicyAudit) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "pricing_policy_audit"}}
}

func (PricingPolicyAudit) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(),
		field.UUID("policy_id", uuid.UUID{}).Immutable(),
		field.UUID("actor_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.Enum("action").Values("DRAFT_CREATED", "PUBLISHED", "WITHDRAWN").Immutable(),
		field.String("reason").MinLen(8).MaxLen(500).Immutable(),
		field.JSON("before_value", json.RawMessage{}).Optional().
			SchemaType(map[string]string{dialect.Postgres: "jsonb"}).Immutable(),
		field.JSON("after_value", json.RawMessage{}).
			SchemaType(map[string]string{dialect.Postgres: "jsonb"}).Immutable(),
		field.Time("occurred_at").Default(time.Now).Immutable(),
	}
}

func (PricingPolicyAudit) Edges() []ent.Edge {
	return []ent.Edge{
		edge.From("policy", PricingPolicy.Type).Ref("audit").Unique().Required().Immutable().Field("policy_id"),
	}
}
