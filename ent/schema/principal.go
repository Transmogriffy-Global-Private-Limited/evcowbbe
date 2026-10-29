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

// Principal is an account in exactly one security plane. Its ID is composite.
type Principal struct{ ent.Schema }

func (Principal) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("principals")} }

func (Principal) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "principals"}}
}

func (Principal) Fields() []ent.Field {
	return []ent.Field{
		// Introduced by migration 006; the original natural key remains unique.
		field.Int64("id").Immutable(),
		field.UUID("account_id", uuid.UUID{}).Immutable(),
		field.Enum("plane").Values("app", "driver", "admin").Immutable(),
		field.Enum("status").Values("ACTIVE", "PENDING", "SUSPENDED"),
		field.Time("created_at").Default(time.Now).Immutable(),
	}
}

func (Principal) Indexes() []ent.Index {
	return []ent.Index{index.Fields("account_id", "plane").Unique().StorageKey("principals_account_id_plane_key")}
}
