package pricing

import (
	"errors"
	"testing"
)

func TestStrictPricingJSON(t *testing.T) {
	good := `{"policy_key":"STANDARD","policy_version":1,"scope":{"kind":"GLOBAL","key":"ALL"},"currency":"INR","reason":"Initial published pricing","rules":[{"id":"VISIT","kind":"BASE","component":"VISIT","basis":"VISITS","rate":"0","tax_class":"EV_SERVICE","priority":0}]}`
	var value DraftRequest
	if err := StrictJSON([]byte(good), &value); err != nil || value.Validate() != nil {
		t.Fatalf("legitimate zero-amount tariff draft invalid: %v / %v", err, value.Validate())
	}
	for _, invalid := range []string{
		good + ` {}`, good + ` garbage`,
		`{"policy_key":"STANDARD","unexpected":1}`,
		`{"policy_key":"STANDARD","scope":{"kind":"GLOBAL","key":"ALL","invented":true}}`,
		`{"policy_key":"STANDARD","policy_key":"OVERRIDDEN"}`,
		`{"policy_key":"STANDARD","rules":[{"id":"VISIT","id":"SURPRISE"}]}`,
		`null`,
	} {
		var request DraftRequest
		if err := StrictJSON([]byte(invalid), &request); !errors.Is(err, ErrInvalidPolicy) {
			t.Fatalf("extra/malformed input accepted %s", invalid)
		}
	}
}

func TestDraftRequiresExplicitRevisionAndReason(t *testing.T) {
	r := DraftRequest{PolicyKey: "STANDARD", Version: 1, Scope: Scope{"GLOBAL", "ALL"}, Currency: "INR", Rules: baseTerms("10").Rules, Reason: "Business approval pending"}
	if err := r.Validate(); err != nil {
		t.Fatal(err)
	}
	r.Reason = "no"
	if err := r.Validate(); !errors.Is(err, ErrInvalidPolicy) {
		t.Fatalf("missing audit reason accepted: %v", err)
	}
	r.Reason = "Business approval pending"
	r.Scope.Kind = "hub id"
	if err := r.Validate(); !errors.Is(err, ErrInvalidPolicy) {
		t.Fatalf("unrecognized scope identifier accepted: %v", err)
	}
}
