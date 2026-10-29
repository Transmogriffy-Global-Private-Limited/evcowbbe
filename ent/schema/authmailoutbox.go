package schema

import (
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// AuthMailOutbox contains encrypted delivery material, not plaintext OTPs.
type AuthMailOutbox struct{ ent.Schema }

func (AuthMailOutbox) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("auth_mail_outbox")} }

func (AuthMailOutbox) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "auth_mail_outbox"}}
}

func (AuthMailOutbox) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Immutable(),
		field.UUID("account_id", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.String("recipient_email"),
		field.Enum("template").Values("verification", "reset", "otp", "security"),
		field.Bytes("payload_ciphertext").Optional().Nillable().Sensitive(),
		field.Bytes("payload_nonce").Optional().Nillable().Sensitive(),
		field.Enum("credential_kind").Values("VERIFY_EMAIL", "RESET_PASSWORD", "OTP").Optional().Nillable(),
		field.Bytes("credential_digest").Optional().Nillable().Sensitive(),
		field.UUID("credential_challenge_id", uuid.UUID{}).Optional().Nillable(),
		field.Time("expires_at").Optional().Nillable(),
		field.Enum("state").Values("PENDING", "LEASED", "SENT", "EXPIRED", "DEAD").Default("PENDING"),
		field.Int32("attempts").NonNegative().Default(0),
		field.Time("next_attempt_at").Default(time.Now),
		field.UUID("lease_token", uuid.UUID{}).Optional().Nillable(),
		field.Time("lease_until").Optional().Nillable(),
		field.String("last_error_class").Optional().Nillable(),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("updated_at").Default(time.Now).UpdateDefault(time.Now),
		field.Time("sent_at").Optional().Nillable(),
	}
}
