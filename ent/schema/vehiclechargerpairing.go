package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
	"github.com/google/uuid"
)

// VehicleChargerPairing tracks physical pairing history, not dispatch eligibility.
type VehicleChargerPairing struct{ ent.Schema }

func (VehicleChargerPairing) Mixin() []ent.Mixin {
	return []ent.Mixin{sqlIntegrity("vehicle_charger_pairings")}
}

func (VehicleChargerPairing) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "vehicle_charger_pairings"}}
}

func (VehicleChargerPairing) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.UUID("vehicle_id", uuid.UUID{}).Immutable(),
		field.UUID("charger_id", uuid.UUID{}).Immutable(),
		field.Time("effective_from").Immutable(),
		field.Time("effective_to").Optional().Nillable(),
		field.Enum("status").Values("ACTIVE", "ENDED", "INVALIDATED").Default("ACTIVE"),
		field.Enum("source").Values("ADMIN", "RECONCILIATION").Default("ADMIN"),
		field.UUID("created_by", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.String("reason").MinLen(8).MaxLen(500),
		field.String("end_reason").Optional().Nillable().MinLen(8).MaxLen(500),
		field.UUID("ended_by", uuid.UUID{}).Optional().Nillable(),
	}
}

func (VehicleChargerPairing) Indexes() []ent.Index {
	return []ent.Index{index.Fields("charger_id").Unique().Annotations(entsql.IndexWhere("status = 'ACTIVE'"))}
}
