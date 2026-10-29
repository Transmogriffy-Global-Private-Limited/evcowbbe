package schema

import (
	"entgo.io/ent"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
	"time"
)

// AuthRateLimit is keyed by bucket and hashed key bytes, never by raw IP/email.
type AuthRateLimit struct{ ent.Schema }

func (AuthRateLimit) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("auth_rate_limits")} }

func (AuthRateLimit) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "auth_rate_limits"}}
}

func (AuthRateLimit) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(), // migration 006; historical compound key remains unique
		field.String("bucket").Immutable(),
		field.Bytes("key_digest").Immutable().Sensitive(),
		field.Time("window_start").Default(time.Now),
		field.Int32("hits").NonNegative(),
	}
}

func (AuthRateLimit) Indexes() []ent.Index {
	return []ent.Index{index.Fields("bucket", "key_digest").Unique().StorageKey("auth_rate_limits_bucket_key_digest_key")}
}
