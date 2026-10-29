package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// ServiceVehicle is a company-operated mobile asset, never a customer's saved EV.
type ServiceVehicle struct{ ent.Schema }

func (ServiceVehicle) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("service_vehicles")} }

func (ServiceVehicle) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.String("asset_tag").Unique().MinLen(2).MaxLen(64).Immutable(),
		field.String("registration_number").Optional().Nillable().Unique().MinLen(2).MaxLen(32),
		field.String("make").MinLen(1).MaxLen(120),
		field.String("model").MinLen(1).MaxLen(120),
		field.String("variant").Optional().Nillable(),
		field.String("vehicle_type").Optional().Nillable(),
		field.String("ownership_type").Optional().Nillable(),
		field.Time("registration_valid_until").Optional().Nillable().SchemaType(map[string]string{dialect.Postgres: "date"}),
		field.Time("insurance_valid_until").Optional().Nillable().SchemaType(map[string]string{dialect.Postgres: "date"}),
		field.Enum("inspection_status").Values("UNKNOWN", "VALID", "FAILED", "EXPIRED").Default("UNKNOWN"),
		field.Enum("operational_status").Values("AVAILABLE", "OUT_OF_SERVICE", "RETIRED").Default("AVAILABLE"),
		field.Enum("maintenance_status").Values("UNKNOWN", "CLEAR", "SCHEDULED", "ACTIVE").Default("UNKNOWN"),
		field.Bool("is_active").Default(true),
		field.String("notes").Optional().Nillable().MaxLen(4000),
		field.UUID("created_by", uuid.UUID{}).Optional().Nillable(),
		field.UUID("updated_by", uuid.UUID{}).Optional().Nillable(),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("updated_at").Default(time.Now).UpdateDefault(time.Now),
		field.Int64("version").Positive().Default(1),
	}
}
