package pricing

import (
	"errors"
	"testing"
	"time"
)

func instant(s string) *time.Time {
	t, err := time.Parse(time.RFC3339, s)
	if err != nil {
		panic(err)
	}
	return &t
}

func policy(id, kind, key string, start, end *time.Time) Policy {
	terms := baseTerms("10")
	terms.PolicyID = id
	return Policy{Scope: Scope{kind, key}, Active: true, Start: start, End: end, Terms: terms}
}

func TestTemporalNestedAndOpenPrecedence(t *testing.T) {
	global := Scope{"GLOBAL", "ALL"}
	policies := []Policy{
		policy("root", "GLOBAL", "ALL", nil, nil),
		policy("october", "GLOBAL", "ALL", instant("2026-10-01T00:00:00Z"), nil),
		policy("promo", "GLOBAL", "ALL", instant("2026-10-25T00:00:00Z"), instant("2026-11-03T00:00:00Z")),
		policy("special", "GLOBAL", "ALL", instant("2026-10-27T00:00:00Z"), instant("2026-10-29T00:00:00Z")),
	}
	checks := []struct{ at, id string }{
		{"2026-09-30T23:59:59Z", "root"},
		{"2026-10-01T00:00:00Z", "october"},
		{"2026-10-25T00:00:00Z", "promo"},
		{"2026-10-28T00:00:00Z", "special"},
		{"2026-10-29T00:00:00Z", "promo"},
		{"2026-11-03T00:00:00Z", "october"},
	}
	for _, check := range checks {
		at := instant(check.at)
		resolved, err := Resolve(policies, []Scope{global}, *at)
		if err != nil || resolved.Terms.PolicyID != check.id {
			t.Fatalf("at %s got %s, %v; want %s", check.at, resolved.Terms.PolicyID, err, check.id)
		}
	}
}

func TestScopePrecedenceNotHubBased(t *testing.T) {
	policies := []Policy{
		policy("default", "GLOBAL", "ALL", nil, nil),
		policy("zone", "SERVICE_ZONE", "ZONE_7", nil, nil),
		policy("tier", "SERVICE_TIER", "PRIORITY", nil, nil),
	}
	order := []Scope{{"SERVICE_TIER", "PRIORITY"}, {"SERVICE_ZONE", "ZONE_7"}, {"GLOBAL", "ALL"}}
	got, err := Resolve(policies, order, *instant("2026-10-28T12:00:00Z"))
	if err != nil || got.Terms.PolicyID != "tier" {
		t.Fatalf("scope precedence: %+v %v", got, err)
	}
	got.Terms.Rules[0].Conditions = map[string]string{"TEST": "CHANGED"}
	if policies[2].Terms.Rules[0].Conditions != nil {
		t.Fatal("returned policy must be a detached snapshot")
	}
}

func TestTemporalConflictRejected(t *testing.T) {
	tests := []struct {
		name     string
		policies []Policy
	}{
		{"double root", []Policy{policy("a", "GLOBAL", "ALL", nil, nil), policy("b", "GLOBAL", "ALL", nil, nil)}},
		{"duplicate future", []Policy{policy("a", "GLOBAL", "ALL", instant("2026-10-01T00:00:00Z"), nil), policy("b", "GLOBAL", "ALL", instant("2026-10-01T00:00:00Z"), nil)}},
		{"crossing", []Policy{policy("a", "GLOBAL", "ALL", instant("2026-10-01T00:00:00Z"), instant("2026-11-01T00:00:00Z")), policy("b", "GLOBAL", "ALL", instant("2026-10-15T00:00:00Z"), instant("2026-11-15T00:00:00Z"))}},
		{"identical bounded", []Policy{policy("a", "GLOBAL", "ALL", instant("2026-10-01T00:00:00Z"), instant("2026-11-01T00:00:00Z")), policy("b", "GLOBAL", "ALL", instant("2026-10-01T00:00:00Z"), instant("2026-11-01T00:00:00Z"))}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if err := ValidateTopology(tc.policies); !errors.Is(err, ErrTemporalConflict) {
				t.Fatalf("conflict not rejected: %v", err)
			}
		})
	}
}

func TestTemporalSafeEdges(t *testing.T) {
	tests := []struct {
		name     string
		policies []Policy
	}{
		{"end without start", []Policy{policy("a", "GLOBAL", "ALL", nil, instant("2026-11-01T00:00:00Z"))}},
		{"equal bounds", []Policy{policy("a", "GLOBAL", "ALL", instant("2026-11-01T00:00:00Z"), instant("2026-11-01T00:00:00Z"))}},
		{"reversed bounds", []Policy{policy("a", "GLOBAL", "ALL", instant("2026-11-02T00:00:00Z"), instant("2026-11-01T00:00:00Z"))}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if err := ValidateTopology(tc.policies); !errors.Is(err, ErrInvalidPolicy) {
				t.Fatalf("invalid interval accepted: %v", err)
			}
		})
	}
	_, err := Resolve([]Policy{policy("later", "GLOBAL", "ALL", instant("2026-11-01T00:00:00Z"), nil)}, []Scope{{"GLOBAL", "ALL"}}, *instant("2026-10-28T00:00:00Z"))
	if !errors.Is(err, ErrNoApplicablePolicy) {
		t.Fatalf("missing policy must fail closed: %v", err)
	}
}

func TestIndependentScopesAndInactiveRoots(t *testing.T) {
	policies := []Policy{
		policy("global", "GLOBAL", "ALL", nil, nil),
		policy("zone", "SERVICE_ZONE", "Z1", nil, nil),
		policy("zone2", "SERVICE_ZONE", "Z2", nil, nil),
	}
	if err := ValidateTopology(policies); err != nil {
		t.Fatal(err)
	}
	policies = append(policies, policy("future", "GLOBAL", "ALL", nil, nil))
	policies[len(policies)-1].Active = false
	if err := ValidateTopology(policies); err != nil {
		t.Fatal(err)
	}
}
