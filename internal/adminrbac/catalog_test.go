package adminrbac

import "testing"

func TestAnnex44CatalogAndDenyPrecedence(t *testing.T) {
	got := Catalog()
	if len(got) != 7 {
		t.Fatalf("role count %d, want 7", len(got))
	}
	if len(Capabilities()) != 45 {
		t.Fatalf("capability count %d, want 45", len(Capabilities()))
	}
	for role, bundle := range got {
		if !ValidRole(role) {
			t.Fatalf("invalid role %s", role)
		}
		seen := map[string]bool{}
		for _, cap := range bundle {
			if !ValidCapability(cap) || seen[cap] {
				t.Fatalf("invalid/duplicated %s in %s", cap, role)
			}
			seen[cap] = true
		}
	}
	full := Evaluate([]string{"FULL_ADMIN"}, nil)
	if len(full) != len(Capabilities()) {
		t.Fatal("full Admin did not receive exact catalog")
	}
	blocked := Evaluate([]string{"FULL_ADMIN"}, map[string]string{"system.settings.manage": "DENY"})
	for _, cap := range blocked {
		if cap == "system.settings.manage" {
			t.Fatal("explicit deny failed")
		}
	}
	if len(blocked) != len(full)-1 {
		t.Fatal("deny removed unexpected capabilities")
	}
	if got := Evaluate([]string{"READ_ONLY"}, nil); contains(got, "audit.read") || contains(got, "users.manage") {
		t.Fatal("read-only elevated")
	}
	if got := Evaluate(nil, map[string]string{"drivers.review": "ALLOW"}); len(got) != 1 || got[0] != "drivers.review" {
		t.Fatal("explicit allow broken")
	}
	if got := Evaluate([]string{"NOT_A_ROLE"}, map[string]string{"*": "ALLOW"}); len(got) != 0 {
		t.Fatal("unknown role or wildcard grants")
	}
}
func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

func TestRBACReasonValidation(t *testing.T) {
	for _, s := range []string{"", "short", "       ", "has\x00nul too"} {
		if validReason(s) {
			t.Fatalf("accepted invalid reason %q", s)
		}
	}
	if !validReason("Documented duty transfer") {
		t.Fatal("rejected valid audit reason")
	}
}
