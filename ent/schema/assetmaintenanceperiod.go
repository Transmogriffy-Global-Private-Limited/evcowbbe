package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

type AssetMaintenancePeriod struct{ ent.Schema }

func (AssetMaintenancePeriod) Mixin() []ent.Mixin {
	return []ent.Mixin{sqlIntegrity("asset_maintenance_periods")}
}

func (AssetMaintenancePeriod) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "asset_maintenance_periods"}}
}

func (AssetMaintenancePeriod) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.UUID("vehicle_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.UUID("charger_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.Time("starts_at").Immutable(),
		field.Time("ends_at").Immutable(),
		field.String("reason").MinLen(8).MaxLen(500).Immutable(),
		field.UUID("created_by", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.Time("created_at").Default(time.Now).Immutable(),
	}
}
