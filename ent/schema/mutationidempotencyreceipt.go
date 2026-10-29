package schema

import (
	"encoding/json"
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema"
	"entgo.io/ent/schema/field"
	"entgo.io/ent/schema/index"
	"github.com/google/uuid"
)

// MutationIdempotencyReceipt is a transaction-bound anti-replay record.
type MutationIdempotencyReceipt struct{ ent.Schema }

func (MutationIdempotencyReceipt) Mixin() []ent.Mixin {
	return []ent.Mixin{sqlIntegrity("mutation_idempotency_receipts")}
}

func (MutationIdempotencyReceipt) Annotations() []schema.Annotation {
	return []schema.Annotation{entsql.Annotation{Table: "mutation_idempotency_receipts"}}
}

func (MutationIdempotencyReceipt) Fields() []ent.Field {
	return []ent.Field{
		field.Int64("id").Immutable(), // migration 006 surrogate; natural key retained
		field.UUID("actor_id", uuid.UUID{}).Immutable(),
		field.String("request_key").MinLen(16).MaxLen(128).Immutable(),
		field.Bytes("request_digest").Immutable().Sensitive(),
		field.Int32("response_status").Immutable(),
		field.JSON("response_body", json.RawMessage{}).SchemaType(map[string]string{dialect.Postgres: "jsonb"}).Immutable(),
		field.Time("created_at").Default(time.Now).Immutable(),
	}
}

func (MutationIdempotencyReceipt) Indexes() []ent.Index {
	return []ent.Index{index.Fields("actor_id", "request_key").Unique().StorageKey("mutation_idempotency_receipts_actor_id_request_key_key")}
}
