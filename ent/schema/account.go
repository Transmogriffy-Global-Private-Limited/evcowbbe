package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// Account is the global authentication identity, not a customer/driver/admin profile.
type Account struct{ ent.Schema }

func (Account) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("accounts")} }

func (Account) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.String("email").Unique().MinLen(3).MaxLen(254),
		field.String("password_hash").Sensitive(),
		field.Time("email_verified_at").Optional().Nillable(),
		field.Bool("two_factor_enabled").Default(false),
		field.Time("disabled_at").Optional().Nillable(),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("updated_at").Default(time.Now).UpdateDefault(time.Now),
	}
}
