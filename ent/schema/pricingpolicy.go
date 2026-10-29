package schema

import (
	"encoding/json"
	"regexp"
	"time"

	"entgo.io/ent"
	"entgo.io/ent/dialect"
	"entgo.io/ent/dialect/entsql"
	"entgo.io/ent/schema/edge"
	"entgo.io/ent/schema/field"
	"github.com/google/uuid"
)

// PricingPolicy maps the immutable/versioned Admin tariff policy contract in
// pending migration 005. Final pricing decisions require the pricing service.
type PricingPolicy struct{ ent.Schema }

func (PricingPolicy) Mixin() []ent.Mixin { return []ent.Mixin{sqlIntegrity("pricing_policies")} }

func (PricingPolicy) Fields() []ent.Field {
	return []ent.Field{
		field.UUID("id", uuid.UUID{}).Default(uuid.New).Immutable(),
		field.String("policy_key").MaxLen(64).Match(regexp.MustCompile(`^[A-Z][A-Z0-9_]{0,63}$`)).Immutable(),
		field.Int64("policy_version").Positive().Immutable(),
		field.String("scope_kind").MaxLen(64).Match(regexp.MustCompile(`^[A-Z][A-Z0-9_]{0,63}$`)).Immutable(),
		field.String("scope_key").MaxLen(160).Match(regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$`)).Immutable(),
		field.Time("starts_at").Optional().Nillable().Immutable(),
		field.Time("ends_at").Optional().Nillable().Immutable(),
		field.String("currency").Validate(func(v string) error { return only(v, "INR") }).Immutable(),
		field.JSON("rules", json.RawMessage{}).SchemaType(map[string]string{dialect.Postgres: "jsonb"}).Immutable(),
		field.Enum("lifecycle").Values("DRAFT", "PUBLISHED", "WITHDRAWN").Default("DRAFT"),
		field.Int64("state_version").Positive().Default(1),
		field.String("reason").MinLen(8).MaxLen(500),
		field.UUID("created_by", uuid.UUID{}).Optional().Nillable().Immutable(),
		field.UUID("published_by", uuid.UUID{}).Optional().Nillable(),
		field.UUID("withdrawn_by", uuid.UUID{}).Optional().Nillable(),
		field.Time("created_at").Default(time.Now).Immutable(),
		field.Time("published_at").Optional().Nillable(),
		field.Time("withdrawn_at").Optional().Nillable(),
	}
}

func (PricingPolicy) Edges() []ent.Edge {
	return []ent.Edge{edge.To("audit", PricingPolicyAudit.Type).Annotations(entsql.OnDelete(entsql.Restrict))}
}
