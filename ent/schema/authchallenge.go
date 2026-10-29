package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

type AuthChallenge struct{ ent.Schema }

func (AuthChallenge) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("auth_challenges")} }

func (AuthChallenge) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.UUID("account_id", uuid.UUID{}).Immutable(),
		field.Enum("plane").Values("app", "driver", "admin").Immutable(),
		field.Enum("purpose").Values("LOGIN_2FA", "ENABLE_2FA", "DISABLE_2FA").Immutable(),
		field.Bytes("digest").Immutable().Sensitive(),
		field.Int32("attempts").NonNegative().Default(0),
		field.Time("expires_at"),
		field.Time("last_sent_at").Default(time.Now),
		field.Time("consumed_at").Optional().Nillable(),
		field.Time("created_at").Default(time.Now).Immutable(),
	}
}
