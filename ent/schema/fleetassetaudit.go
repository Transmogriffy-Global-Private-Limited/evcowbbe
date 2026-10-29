package schema

import (
	"encoding/json"
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

type FleetAssetAudit struct{ ent.Schema }

func (FleetAssetAudit) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("fleet_asset_audit")} }

func (FleetAssetAudit) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "fleet_asset_audit"}}
}

func (FleetAssetAudit) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(),
		field.UUID("actor_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.Enum("asset_kind").Values("VEHICLE", "CHARGER", "CONNECTOR", "PAIRING", "MAINTENANCE").Immutable(),
		field.UUID("asset_id", uuid.UUID{}).Immutable(),
		field.String("action").Immutable(),
		field.Int64("version").Optional().Nillable().Immutable(),
		field.String("reason").Optional().Nillable().Immutable(),
		field.JSON("before_value", json.RawMessage{}).Optional().SchemaType(map[string]string{dialect.Postgres: "jsonb"}).Immutable(),
		field.JSON("after_value", json.RawMessage{}).Optional().SchemaType(map[string]string{dialect.Postgres: "jsonb"}).Immutable(),
		field.Time("created_at").Default(time.Now).Immutable(),
	}
}
