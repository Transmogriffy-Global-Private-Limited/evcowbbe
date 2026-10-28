package api

import (
	"net/http/httptest"
	"testing"
)

func TestFleetRoutesAndPreconditions(t *testing.T) {
	for _, path := range []string{"vehicles", "vehicles/123", "chargers", "chargers/abc/connectors", "pairings"} {
		if !isFleetRoute(path) {
			t.Fatalf("valid fleet route not recognized: %s", path)
		}
	}
	for _, path := range []string{"vehicle", "fleet", "rbac/me", "chargers-old"} {
		if isFleetRoute(path) {
			t.Fatalf("unexpected fleet route: %s", path)
		}
	}
	tests := []struct {
		h       string
		version int64
		status  int
	}{{"", 0, 428}, {"W/\"12\"", 0, 400}, {"12", 0, 400}, {"\"0\"", 0, 400}, {"\"3\"", 3, 0}, {"\"12\"", 12, 0}}
	for _, tc := range tests {
		r := httptest.NewRequest("PATCH", "/api/v1/admin/vehicles", nil)
		if tc.h != "" {
			r.Header.Set("If-Match", tc.h)
		}
		v, code := fleetPrecondition(r)
		if v != tc.version || code != tc.status {
			t.Fatalf("If-Match %q => %d/%d, expected %d/%d", tc.h, v, code, tc.version, tc.status)
		}
	}
}

func TestFleetQueryGuard(t *testing.T) {
	valid := []string{"/api/v1/admin/vehicles", "/api/v1/admin/vehicles?limit=100&active=true&q=ace", "/api/v1/admin/vehicles?status=AVAILABLE"}
	for _, uri := range valid {
		r := httptest.NewRequest("GET", uri, nil)
		if _, err := parseFleetFilter(r); err != nil {
			t.Fatalf("rejected %s: %v", uri, err)
		}
	}
	invalid := []string{"/api/v1/admin/vehicles?limit=-1", "/api/v1/admin/vehicles?limit=500", "/api/v1/admin/vehicles?unused=value", "/api/v1/admin/vehicles?limit=1&limit=2", "/api/v1/admin/vehicles?active=maybe"}
	for _, uri := range invalid {
		r := httptest.NewRequest("GET", uri, nil)
		if _, err := parseFleetFilter(r); err == nil {
			t.Fatalf("accepted %s", uri)
		}
	}
}

func TestFleetControlJSONKeys(t *testing.T) {
	if !jsonKeysOnly([]byte(`{"reason":"A documented change"}`), "reason") {
		t.Fatal("valid mutation rejected")
	}
	if jsonKeysOnly([]byte(`{"reason":"A documented change","skip_audit":true}`), "reason") {
		t.Fatal("unknown mutation field accepted")
	}
}
