package api

import "testing"

func TestPricingRouteClassifier(t *testing.T) {
	for _, action := range []string{"pricing/policies", "pricing/policies/00000000-0000-4000-8000-000000000001", "pricing/policies/00000000-0000-4000-8000-000000000001/publish"} {
		if !isPricingRoute(action) {
			t.Fatalf("pricing route not recognized: %s", action)
		}
	}
	for _, action := range []string{"pricing", "pricing/policy", "pricing/policies-wrong", "payments", "vehicles"} {
		if isPricingRoute(action) {
			t.Fatalf("unrelated route recognized: %s", action)
		}
	}
}
