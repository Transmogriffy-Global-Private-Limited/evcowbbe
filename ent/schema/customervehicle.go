package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
	"github.com/lib/pq"
)

// CustomerVehicle is the user's saved vehicle, not the service fleet asset.
type CustomerVehicle struct{ ent.Schema }

func (CustomerVehicle) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("customer_vehicles")} }

func (CustomerVehicle) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Default(uuid.New).Immutable(),
		field.UUID("customer_id", uuid.UUID{}).Immutable(),
		field.String("label").Optional().Nillable().NotEmpty().MaxLen(100),
		field.String("make").NotEmpty().MaxLen(120),
		field.String("model").NotEmpty().MaxLen(120),
		field.String("variant").Optional().Nillable(),
		field.String("registration_number").Optional().Nillable().MinLen(2).MaxLen(32),
		field.Int64("battery_capacity_wh").Optional().Nillable().Positive(),
		field.Bool("supports_ac").Optional().Nillable(),
		field.Bool("supports_dc").Optional().Nillable(),
		field.Int32("max_ac_power_w").Optional().Nillable().Positive(),
		field.Int32("max_dc_power_w").Optional().Nillable().Positive(),
		// PostgreSQL text[], not Ent's JSON-encoded field.Strings.
		field.Other("connector_types", &pq.StringArray{}).
			SchemaType(map[string]string{dialect.Postgres: "text[]"}).Default(&pq.StringArray{}),
		field.Bool("is_active").Default(true),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("updated_at").Default(time.Now).UpdateDefault(time.Now),
		field.Int64("version").Positive().Default(1),
	}
}

func (CustomerVehicle) Edges() []ent.Edge {
	return []ent.Edge{
		edge.From("customer", CustomerProfile.Type).
			Ref("vehicles").Unique().Required().Immutable().Field("customer_id"),
	}
}
