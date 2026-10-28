package pricing

import (
	"errors"
	"fmt"
	"regexp"
	"time"
)

var ErrTemporalConflict = errors.New("ambiguous temporal pricing policies")
var ErrNoApplicablePolicy = errors.New("no applicable pricing policy")

var scopeKey = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$`)

// Scope represents an operational identity, not a CMS hub. Callers supply
// their explicitly approved precedence order, with most specific scope first;
// this package cannot infer a customer's groups, location, or asset class.
type Scope struct {
	Kind string `json:"kind"`
	Key  string `json:"key"`
}

// Policy represents a published or withdrawn immutable revision. Start is
// inclusive, end exclusive. A nil start and end represent the scope root.
// A future open-ended policy is a fallback when no bounded override applies.
type Policy struct {
	Scope  Scope      `json:"scope"`
	Active bool       `json:"active"`
	Start  *time.Time `json:"starts_at,omitempty"`
	End    *time.Time `json:"ends_at,omitempty"`
	Terms  Terms      `json:"terms"`
}

func validScope(s Scope) bool {
	return validID(s.Kind) && scopeKey.MatchString(s.Key)
}

func validateInterval(start, end *time.Time) error {
	if start == nil && end != nil || start != nil && end != nil && !start.Before(*end) {
		return ErrInvalidPolicy
	}
	return nil
}

func overlaps(a, b Policy) bool {
	return a.Start.Before(*b.End) && b.Start.Before(*a.End)
}

func containsStrict(outer, inner Policy) bool {
	return !outer.Start.After(*inner.Start) && !outer.End.Before(*inner.End) &&
		(outer.Start.Before(*inner.Start) || inner.End.Before(*outer.End))
}

// ValidateTopology rejects ambiguous active revisions at every exact scope.
// Nested bounded windows are legal, while crossing, identical, and duplicate
// open-start windows are illegal. Inactive records never affect resolution.
func ValidateTopology(policies []Policy) error {
	byScope := make(map[Scope][]Policy)
	seenVersions := make(map[string]bool)
	for _, policy := range policies {
		if !validScope(policy.Scope) || validateInterval(policy.Start, policy.End) != nil || Validate(policy.Terms) != nil {
			return ErrInvalidPolicy
		}
		versionKey := fmt.Sprintf("%s/%d", policy.Terms.PolicyID, policy.Terms.PolicyVersion)
		if seenVersions[versionKey] {
			return ErrTemporalConflict
		}
		seenVersions[versionKey] = true
		if policy.Active {
			byScope[policy.Scope] = append(byScope[policy.Scope], policy)
		}
	}
	for _, group := range byScope {
		rootCount := 0
		openStarts := map[time.Time]bool{}
		var bounded []Policy
		for _, policy := range group {
			switch {
			case policy.Start == nil:
				rootCount++
			case policy.End == nil:
				key := policy.Start.UTC()
				if openStarts[key] {
					return ErrTemporalConflict
				}
				openStarts[key] = true
			default:
				bounded = append(bounded, policy)
			}
		}
		if rootCount > 1 {
			return ErrTemporalConflict
		}
		for i, first := range bounded {
			for _, second := range bounded[i+1:] {
				if overlaps(first, second) && !containsStrict(first, second) && !containsStrict(second, first) {
					return ErrTemporalConflict
				}
			}
		}
	}
	return nil
}

func atTime(policy Policy, at time.Time) bool {
	return (policy.Start == nil || !at.Before(*policy.Start)) && (policy.End == nil || at.Before(*policy.End))
}

func moreSpecific(a, b Policy) bool {
	// Both are bounded and active at the same instant. Strict containment was
	// already checked, so later start/earlier end identifies the deepest one.
	return b.Start.Before(*a.Start) || b.Start.Equal(*a.Start) && a.End.Before(*b.End)
}

func cloneTerms(t Terms) Terms {
	out := t
	out.Rules = make([]Rule, len(t.Rules))
	for i, rule := range t.Rules {
		out.Rules[i] = rule
		if rule.Conditions != nil {
			out.Rules[i].Conditions = make(map[string]string, len(rule.Conditions))
			for k, v := range rule.Conditions {
				out.Rules[i].Conditions[k] = v
			}
		}
	}
	return out
}

// Resolve selects the first applicable scope from an externally authorized
// precedence list. Within a scope: deepest bounded override, newest eligible
// open-ended policy, then root. No silent fallback across ambiguous scopes.
func Resolve(policies []Policy, precedence []Scope, at time.Time) (Policy, error) {
	if at.IsZero() || len(precedence) == 0 || ValidateTopology(policies) != nil {
		return Policy{}, ErrInvalidPolicy
	}
	used := make(map[Scope]bool)
	for _, scope := range precedence {
		if !validScope(scope) || used[scope] {
			return Policy{}, ErrInvalidPolicy
		}
		used[scope] = true
		var bounded, open, root *Policy
		for _, policy := range policies {
			if !policy.Active || policy.Scope != scope || !atTime(policy, at) {
				continue
			}
			candidate := policy
			switch {
			case policy.Start != nil && policy.End != nil:
				if bounded == nil || moreSpecific(policy, *bounded) {
					bounded = &candidate
				}
			case policy.Start != nil:
				if open == nil || policy.Start.After(*open.Start) {
					open = &candidate
				}
			default:
				root = &candidate
			}
		}
		for _, candidate := range []*Policy{bounded, open, root} {
			if candidate != nil {
				copy := *candidate
				copy.Terms = cloneTerms(candidate.Terms)
				return copy, nil
			}
		}
	}
	return Policy{}, ErrNoApplicablePolicy
}
