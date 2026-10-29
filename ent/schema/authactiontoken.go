package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// AuthActionToken uses the opaque token digest as its SQL primary key.
type AuthActionToken struct{ ent.Schema }

func (AuthActionToken) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("auth_action_tokens")} }

func (AuthActionToken) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(), // migration 006; digest remains unique
		field.Bytes("digest").Unique().Immutable().Sensitive(),
		field.UUID("account_id", uuid.UUID{}).Immutable(),
		field.Enum("kind").Values("VERIFY_EMAIL", "RESET_PASSWORD").Immutable(),
		field.Time("expires_at"),
		field.Time("consumed_at").Optional().Nillable(),
		field.Time("created_at").Default(time.Now).Immutable(),
	}
}
