package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
)

func corsTestResponse(method, path, origin, requestMethod, requestHeaders string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, nil)
	if origin != "" {
		r.Header.Set("Origin", origin)
	}
	if requestMethod != "" {
		r.Header.Set("Access-Control-Request-Method", requestMethod)
	}
	if requestHeaders != "" {
		r.Header.Set("Access-Control-Request-Headers", requestHeaders)
	}
	w := httptest.NewRecorder()
	(&Server{C: config.Config{Env: "staging"}}).Handler().ServeHTTP(w, r)
	return w
}

func TestCORSDevelopmentOriginsAndPreflight(t *testing.T) {
	for _, host := range []string{"localhost", "127.0.0.1"} {
		for _, port := range []string{"5173", "8080", "8081"} {
			origin := "http://" + host + ":" + port
			t.Run(origin, func(t *testing.T) {
				w := corsTestResponse("OPTIONS", "/api/v1/app/auth/login", origin, "POST", "content-type, authorization, X-Request-ID")
				if w.Code != http.StatusNoContent || w.Body.Len() != 0 {
					t.Fatalf("preflight status %d, body %q", w.Code, w.Body.String())
				}
				if got := w.Header().Get("Access-Control-Allow-Origin"); got != origin {
					t.Fatalf("allow-origin %q", got)
				}
				if !strings.Contains(w.Header().Get("Access-Control-Allow-Headers"), "Authorization") ||
					!strings.Contains(w.Header().Get("Access-Control-Allow-Headers"), "If-Match") ||
					!strings.Contains(w.Header().Get("Access-Control-Allow-Methods"), "POST") {
					t.Fatal("missing required methods or headers")
				}
				if got := w.Header().Get("Access-Control-Allow-Credentials"); got != "" {
					t.Fatalf("unexpected cross-origin credential grant %q", got)
				}
				for _, vary := range []string{"Origin", "Access-Control-Request-Method", "Access-Control-Request-Headers"} {
					found := false
					for _, v := range w.Header().Values("Vary") {
						if v == vary {
							found = true
						}
					}
					if !found {
						t.Fatalf("missing Vary: %s", vary)
					}
				}
				// Actual success and error responses both need the same origin;
				// browser clients must be able to read 401/403/422 bodies.
				for _, tc := range []struct {
					path string
					want int
				}{
					{"/version", 200},
					{"/api/v1/admin/me", 401},
				} {
					actual := corsTestResponse("GET", tc.path, origin, "", "")
					if actual.Code != tc.want || actual.Header().Get("Access-Control-Allow-Origin") != origin ||
						!strings.Contains(actual.Header().Get("Access-Control-Expose-Headers"), "X-Request-ID") {
						t.Fatalf("actual %s: status %d, allow-origin %q", tc.path, actual.Code, actual.Header().Get("Access-Control-Allow-Origin"))
					}
				}
			})
		}
	}
}

func TestCORSDoesNotTrustArbitraryOriginsMethodsOrHeaders(t *testing.T) {
	for _, origin := range []string{"https://attacker.example", "null", "http://localhost:5174", "http://localhost:5173.evil.example", "http://127.0.0.1:5173/"} {
		w := corsTestResponse("OPTIONS", "/api/v1/driver/auth/login", origin, "POST", "authorization")
		if w.Code != http.StatusForbidden || w.Header().Get("Access-Control-Allow-Origin") != "" {
			t.Errorf("origin %q incorrectly approved: status %d", origin, w.Code)
		}
		actual := corsTestResponse("GET", "/version", origin, "", "")
		if actual.Code != 200 || actual.Header().Get("Access-Control-Allow-Origin") != "" {
			t.Errorf("actual response exposed to %q", origin)
		}
	}
	for _, tc := range []struct {
		method, headers string
	}{
		{"TRACE", "authorization"},
		{"", "authorization"},
		{"POST", "x-forbidden-admin-bypass"},
		{"POST", "authorization, x-forbidden-admin-bypass"},
	} {
		w := corsTestResponse("OPTIONS", "/api/v1/admin/me", "http://localhost:5173", tc.method, tc.headers)
		if w.Code != 400 || w.Header().Get("Access-Control-Allow-Origin") != "" {
			t.Errorf("invalid preflight approved: %+v status %d", tc, w.Code)
		}
	}
	// Plain OPTIONS, as opposed to browser preflight, retains HTTP 405.
	w := corsTestResponse("OPTIONS", "/api/v1/app/auth/login", "", "", "")
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("bare OPTIONS changed contract: %d", w.Code)
	}
	// Unknown routes should not be invented by the CORS middleware.
	w = corsTestResponse("OPTIONS", "/definitely-not-an-api", "http://localhost:5173", "GET", "")
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("unknown path preflight unexpectedly accepted: %d", w.Code)
	}
}
