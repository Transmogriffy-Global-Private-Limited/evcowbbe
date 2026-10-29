package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// ConsumedRefreshToken uses the hash bytes as its SQL primary key.
type ConsumedRefreshToken struct{ ent.Schema }

func (ConsumedRefreshToken) Mixin() []ent.Mixin {
	return []ent.Mixin{sqlIntegrity("consumed_refresh_tokens")}
}

func (ConsumedRefreshToken) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "consumed_refresh_tokens"}}
}

func (ConsumedRefreshToken) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(), // migration 006; digest remains unique
		field.Bytes("digest").Unique().Immutable().Sensitive(),
		field.UUID("session_id", uuid.UUID{}).Immutable(),
		field.Time("consumed_at").Default(time.Now).Immutable(),
	}
}
