package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// CustomerProfile maps customer_profiles from migration 004. The database owns
// the composite (account_id, plane) FK into principals; no shadow auth models.
type CustomerProfile struct{ ent.Schema }

func (CustomerProfile) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Default(uuid.New).Immutable(),
		field.UUID("account_id", uuid.UUID{}).Unique().Immutable(),
		field.String("plane").Default("app").Immutable().Validate(func(v string) error { return only(v, "app") }),
		field.String("full_name").Optional().Nillable().NotEmpty().MaxLen(160),
		field.String("primary_phone").Optional().Nillable().MinLen(7).MaxLen(32),
		field.Enum("status").Values("INCOMPLETE", "ACTIVE", "SUSPENDED", "CLOSED").Default("INCOMPLETE"),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("updated_at").Default(time.Now).UpdateDefault(time.Now),
		field.Int64("version").Positive().Default(1),
	}
}

func (CustomerProfile) Edges() []ent.Edge {
	return []ent.Edge{
		edge.To("vehicles", CustomerVehicle.Type),
		edge.To("addresses", CustomerAddress.Type),
		edge.To("audit", CustomerMasterAudit.Type),
	}
}
