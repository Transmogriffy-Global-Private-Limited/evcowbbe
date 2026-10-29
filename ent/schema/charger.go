package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// Charger is an inventory record, not a live HAL telemetry model.
type Charger struct{ ent.Schema }

func (Charger) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("chargers")} }

func (Charger) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.String("asset_tag").Unique().MinLen(2).MaxLen(64).Immutable(),
		field.String("serial_number").Optional().Nillable().Unique().MinLen(2).MaxLen(120),
		field.String("manufacturer").MinLen(1).MaxLen(120),
		field.String("model").MinLen(1).MaxLen(120),
		field.Int32("rated_power_w").Optional().Nillable().Positive(),
		field.String("protocol_family").Optional().Nillable(),
		field.String("protocol_version").Optional().Nillable(),
		field.String("hal_identity").Optional().Nillable().Unique(),
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
