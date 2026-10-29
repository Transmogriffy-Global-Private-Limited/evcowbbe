package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
	"github.com/shopspring/decimal"
)

// CustomerAddress stores reusable addresses, not ephemeral service locations.
type CustomerAddress struct{ ent.Schema }

func (CustomerAddress) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("customer_addresses")} }

func (CustomerAddress) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Default(uuid.New).Immutable(),
		field.UUID("customer_id", uuid.UUID{}).Immutable(),
		field.String("label").Optional().Nillable().NotEmpty().MaxLen(80),
		field.String("line1").NotEmpty().MaxLen(240),
		field.String("line2").Optional().Nillable(),
		field.String("locality").Optional().Nillable(),
		field.String("city").NotEmpty().MaxLen(120),
		field.String("state").NotEmpty().MaxLen(120),
		field.String("postal_code").Optional().Nillable().MinLen(3).MaxLen(16),
		field.String("country_code").Default("IN").MinLen(2).MaxLen(2),
		field.Other("latitude", &decimal.Decimal{}).Optional().Nillable().
			SchemaType(map[string]string{dialect.Postgres: "numeric(9,6)"}),
		field.Other("longitude", &decimal.Decimal{}).Optional().Nillable().
			SchemaType(map[string]string{dialect.Postgres: "numeric(9,6)"}),
		field.String("landmark").Optional().Nillable(),
		field.String("building_instructions").Optional().Nillable(),
		field.String("floor").Optional().Nillable(),
		field.String("contact_instructions").Optional().Nillable(),
		field.String("geocode_provider").Optional().Nillable(),
		field.String("geocode_external_ref").Optional().Nillable(),
		field.Bool("is_default").Default(false),
		field.Bool("is_active").Default(true),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("updated_at").Default(time.Now).UpdateDefault(time.Now),
		field.Int64("version").Positive().Default(1),
	}
}

func (CustomerAddress) Edges() []ent.Edge {
	return []ent.Edge{
		edge.From("customer", CustomerProfile.Type).
			Ref("addresses").Unique().Required().Immutable().Field("customer_id"),
	}
}
