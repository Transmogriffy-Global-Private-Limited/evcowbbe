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

type ChargerConnector struct{ ent.Schema }

func (ChargerConnector) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("charger_connectors")} }

func (ChargerConnector) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "charger_connectors"}}
}

func (ChargerConnector) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.UUID("charger_id", uuid.UUID{}).Immutable(),
		field.Int32("connector_number").Positive().Immutable(),
		field.String("connector_type").MinLen(2).MaxLen(64),
		field.Int32("max_power_w").Positive(),
		field.Bool("supports_ac"),
		field.Bool("supports_dc"),
		field.Bool("is_active").Default(true),
		field.UUID("created_by", uuid.UUID{}).Optional().Nillable(),
		field.UUID("updated_by", uuid.UUID{}).Optional().Nillable(),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("updated_at").Default(time.Now).UpdateDefault(time.Now),
		field.Int64("version").Positive().Default(1),
	}
}

func (ChargerConnector) Indexes() []ent.Index {
	return []ent.Index{index.Fields("charger_id", "connector_number").Unique()}
}
