package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// AuthSession stores opaque credential digests, never raw tokens.
type AuthSession struct{ ent.Schema }

func (AuthSession) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("auth_sessions")} }

func (AuthSession) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.UUID("account_id", uuid.UUID{}).Immutable(),
		field.Enum("plane").Values("app", "driver", "admin").Immutable(),
		field.Bytes("access_digest").Unique().Sensitive(),
		field.Bytes("refresh_digest").Unique().Sensitive(),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("access_expires_at"),
		field.Time("refresh_expires_at"),
		field.Time("absolute_expires_at"),
		field.Int32("refresh_generation").Default(0).NonNegative(),
		field.Time("last_seen_at").Optional().Nillable(),
		field.Time("revoked_at").Optional().Nillable(),
		field.String("revoke_reason").Optional().Nillable(),
	}
}
