package pricing

import (
	"errors"
	"testing"
)

func baseTerms(rate string) Terms {
	return Terms{PolicyID: "policy-1", PolicyVersion: 1, Currency: "INR", Rules: []Rule{{
		ID: "ENERGY", Kind: BaseRule, Component: "CHARGING_ENERGY", Basis: "METER_KWH", Rate: rate, TaxClass: "EV_SERVICE",
	}}}
}

func facts(q string, estimate bool) Evidence {
	return Evidence{Measurements: map[string]Measurement{"METER_KWH": {Quantity: q, Source: "HAL_TX_1", Estimate: estimate}}}
}

func taxWith(sgst, cgst, igst string) map[string]TaxClass {
	return map[string]TaxClass{"EV_SERVICE": {Approved: true, SGST: sgst, CGST: cgst, IGST: igst}}
}

func TestEnergyPricingExactCMSConcept(t *testing.T) {
	// Illustrative test tax rates only, not a real-world tax decision.
	result, err := Evaluate(baseTerms("16.91"), facts("7.2", false), taxWith("9", "9", "0"), Final)
	if err != nil {
		t.Fatal(err)
	}
	if result.SubtotalMinor != 12175 || result.TaxMinor != 2192 || result.TotalMinor != 14367 {
		t.Fatalf("unexpected exact billing arithmetic: %+v", result)
	}
	var sum int64
	for _, line := range result.Lines {
		sum += line.AmountMinor
	}
	if sum != result.TotalMinor {
		t.Fatalf("sum(lines)=%d total=%d", sum, result.TotalMinor)
	}
}

func TestComposedMobileRules(t *testing.T) {
	terms := Terms{PolicyID: "mobile-p", PolicyVersion: 3, Currency: "INR", Rules: []Rule{
		{ID: "TRAVEL", Kind: BaseRule, Component: "TRAVEL_DISTANCE", Basis: "ROUTE_KM", Rate: "12", TaxClass: "EV_SERVICE"},
		{ID: "VISIT", Kind: BaseRule, Component: "VISIT", Basis: "VISITS", Rate: "100", TaxClass: "EV_SERVICE"},
		{ID: "VISIT_MULT", Kind: MultiplierRule, Target: "VISIT", Rate: "120", Priority: 1},
		{ID: "VISIT_DISC", Kind: DiscountRule, Target: "VISIT", Rate: "10", Priority: 2},
	}}
	evidence := Evidence{Measurements: map[string]Measurement{
		"VISITS":   {Quantity: "1", Source: "JOB_1"},
		"ROUTE_KM": {Quantity: "5.5", Source: "ROUTE_1"},
	}}
	r, err := Evaluate(terms, evidence, taxWith("0", "0", "0"), Final)
	if err != nil {
		t.Fatal(err)
	}
	// Travel 66.00 and visit 100 * 1.2 * 0.9 = 108.00.
	if r.SubtotalMinor != 17400 || r.TotalMinor != 17400 {
		t.Fatalf("unexpected result: %+v", r)
	}
	if len(r.Lines) != 4 {
		t.Fatalf("expected four explicit price rows, got %d", len(r.Lines))
	}
}

func TestOverridePrecedesModifiers(t *testing.T) {
	terms := baseTerms("10")
	terms.Rules = append(terms.Rules,
		Rule{ID: "OVERRIDE", Kind: OverrideRule, Target: "ENERGY", Rate: "12"},
		Rule{ID: "DISCOUNT", Kind: DiscountRule, Target: "ENERGY", Rate: "25"})
	r, err := Evaluate(terms, facts("2", false), taxWith("0", "0", "0"), Final)
	if err != nil {
		t.Fatal(err)
	}
	if r.SubtotalMinor != 1800 {
		t.Fatalf("override and discount = %+v", r)
	}
}

func TestFinalizationRejectsEstimatedEvidence(t *testing.T) {
	_, err := Evaluate(baseTerms("10"), facts("2", true), taxWith("0", "0", "0"), Final)
	if !errors.Is(err, ErrMissingEvidence) {
		t.Fatalf("wanted missing evidence, got %v", err)
	}
	r, err := Evaluate(baseTerms("10"), facts("2", true), taxWith("0", "0", "0"), Preview)
	if err != nil || !r.Estimated {
		t.Fatalf("quote estimate not marked: %+v %v", r, err)
	}
}

func TestNoTaxGuessOrMix(t *testing.T) {
	for _, tax := range []map[string]TaxClass{nil, taxWith("9", "9", "18"), {"EV_SERVICE": {Approved: false, SGST: "0", CGST: "0", IGST: "0"}}} {
		if _, err := Evaluate(baseTerms("10"), facts("1", false), tax, Final); !errors.Is(err, ErrTaxUnavailable) {
			t.Fatalf("unsupported tax profile accepted: %v", err)
		}
	}
}

func TestRejectsUntrustedAndAmbiguousPolicies(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*Terms)
	}{
		{"bad unit rate", func(v *Terms) { v.Rules[0].Rate = "16.91/kWh" }},
		{"negative rate", func(v *Terms) { v.Rules[0].Rate = "-1" }},
		{"duplicate id", func(v *Terms) { v.Rules = append(v.Rules, v.Rules[0]) }},
		{"unknown target", func(v *Terms) {
			v.Rules = append(v.Rules, Rule{ID: "X", Kind: DiscountRule, Target: "GHOST", Rate: "5"})
		}},
		{"two overrides", func(v *Terms) {
			v.Rules = append(v.Rules, Rule{ID: "A", Kind: OverrideRule, Target: "ENERGY", Rate: "1"}, Rule{ID: "B", Kind: OverrideRule, Target: "ENERGY", Rate: "2"})
		}},
		{"too large discount", func(v *Terms) {
			v.Rules = append(v.Rules, Rule{ID: "A", Kind: DiscountRule, Target: "ENERGY", Rate: "101"})
		}},
		{"invalid condition", func(v *Terms) { v.Rules[0].Conditions = map[string]string{"unknown field": "YES"} }},
		{"duplicate base component", func(v *Terms) {
			v.Rules = append(v.Rules, Rule{ID: "DUPLICATE", Kind: BaseRule, Component: "CHARGING_ENERGY", Basis: "METER_KWH", Rate: "1", TaxClass: "EV_SERVICE"})
		}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			v := baseTerms("10")
			tc.mutate(&v)
			if err := Validate(v); !errors.Is(err, ErrInvalidPolicy) {
				t.Fatalf("invalid policy accepted: %v", err)
			}
		})
	}
}

func TestNoFloatingPointRoundingBoundary(t *testing.T) {
	r, err := Evaluate(baseTerms("0.005"), facts("1", false), taxWith("0", "0", "0"), Final)
	if err != nil {
		t.Fatal(err)
	}
	if r.TotalMinor != 1 {
		t.Fatalf("half paise must half-up to 1, got %d", r.TotalMinor)
	}
}

func TestMoneyOverflow(t *testing.T) {
	_, err := Evaluate(baseTerms("999999999999"), facts("999999999999", false), taxWith("0", "0", "0"), Final)
	if !errors.Is(err, ErrAmountOverflow) {
		t.Fatalf("overflow was not rejected: %v", err)
	}
}

func TestMissingConditionalFactsNotInvented(t *testing.T) {
	v := baseTerms("10")
	v.Rules[0].Conditions = map[string]string{"SERVICE_CLASS": "FAST"}
	_, err := Evaluate(v, facts("1", false), nil, Preview)
	if !errors.Is(err, ErrMissingEvidence) {
		t.Fatalf("missing condition must not silently omit a component: %v", err)
	}
	_, err = Evaluate(v, Evidence{Conditions: map[string]string{"SERVICE_CLASS": "NORMAL"}}, nil, Preview)
	if !errors.Is(err, ErrNoApplicablePolicy) {
		t.Fatalf("unmatched policy must not silently return free quote: %v", err)
	}
	_, err = Evaluate(v, Evidence{Conditions: map[string]string{"SERVICE_CLASS": "FAST"}}, nil, Preview)
	if !errors.Is(err, ErrMissingEvidence) {
		t.Fatalf("matched rule must require fact: %v", err)
	}
}
