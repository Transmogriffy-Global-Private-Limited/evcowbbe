package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
	"github.com/lib/pq"
)

// CustomerMasterAudit is immutable evidence. Insert via the owning business
// transaction; business rules must never be delegated to Ent lifecycle hooks.
type CustomerMasterAudit struct{ ent.Schema }

func (CustomerMasterAudit) Mixin() []ent.Mixin {
	return []ent.Mixin{sqlIntegrity("customer_master_audit")}
}

func (CustomerMasterAudit) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "customer_master_audit"}}
}

func (CustomerMasterAudit) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(),
		field.UUID("customer_id", uuid.UUID{}).Immutable(),
		field.UUID("actor_account_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.Enum("resource_kind").Values("PROFILE", "VEHICLE", "ADDRESS").Immutable(),
		field.UUID("resource_id", uuid.UUID{}).Immutable(),
		field.String("action").NotEmpty().Immutable(),
		field.Int64("resulting_version").Positive().Immutable(),
		field.Other("changed_fields", &pq.StringArray{}).
			SchemaType(map[string]string{dialect.Postgres: "text[]"}).Default(&pq.StringArray{}).Immutable(),
		field.Time("occurred_at").Default(time.Now).Immutable(),
	}
}

func (CustomerMasterAudit) Edges() []ent.Edge {
	return []ent.Edge{
		edge.From("customer", CustomerProfile.Type).Ref("audit").Unique().Required().Immutable().Field("customer_id"),
	}
}
