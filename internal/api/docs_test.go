package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/docs/contracts"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
)

func TestDocumentationRoutesWithoutExternalServices(t *testing.T) {
	s := &Server{C: config.Config{Env: "staging"}}
	cases := []struct {
		path, contentType, contentSubstring string
	}{
		{"/swagger/", "text/html; charset=utf-8", "/swagger/swagger-ui-bundle.js"},
		{"/swagger/index.html", "text/html; charset=utf-8", "/swagger/init.js"},
		{"/swagger/init.js", "text/javascript; charset=utf-8", "queryConfigEnabled: false"},
		{"/swagger/swagger-ui.css", "text/css; charset=utf-8", ".swagger-ui"},
		{"/swagger/swagger-ui-bundle.js", "text/javascript; charset=utf-8", "SwaggerUIBundle"},
		{"/swagger/LICENSE", "text/plain; charset=utf-8", "Apache License"},
		{"/swagger/swagger-ui-bundle.js.LICENSE.txt", "text/plain; charset=utf-8", "Copyright"},
		{"/openapi.yaml", "application/yaml; charset=utf-8", "- url: /"},
	}
	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodGet, tc.path, nil)
			w := httptest.NewRecorder()
			s.Handler().ServeHTTP(w, r)
			if w.Code != http.StatusOK {
				t.Fatalf("GET %s returned HTTP %d", tc.path, w.Code)
			}
			if contentType := w.Header().Get("Content-Type"); contentType != tc.contentType {
				t.Fatalf("content type %q; expected %q", contentType, tc.contentType)
			}
			if !strings.Contains(w.Body.String(), tc.contentSubstring) {
				t.Fatalf("GET %s did not include expected content", tc.path)
			}
			for _, name := range []string{"Content-Security-Policy", "X-Content-Type-Options", "X-Robots-Tag"} {
				if w.Header().Get(name) == "" {
					t.Fatalf("GET %s missing %s", tc.path, name)
				}
			}
			if got := w.Header().Get("Cache-Control"); got != "no-store" {
				t.Fatalf("GET %s cache policy %q", tc.path, got)
			}
		})
	}

	redirect := httptest.NewRecorder()
	s.Handler().ServeHTTP(redirect, httptest.NewRequest(http.MethodGet, "/swagger", nil))
	if redirect.Code != http.StatusPermanentRedirect || redirect.Header().Get("Location") != "/swagger/" {
		t.Fatal("/swagger must redirect to /swagger/")
	}
	for _, path := range []string{"/swagger/", "/openapi.yaml", "/swagger/init.js"} {
		rec := httptest.NewRecorder()
		s.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodHead, path, nil))
		if rec.Code != 200 || rec.Body.Len() != 0 {
			t.Fatalf("HEAD %s: status %d, body bytes %d", path, rec.Code, rec.Body.Len())
		}
	}
	for _, path := range []string{"/swagger/", "/openapi.yaml"} {
		rec := httptest.NewRecorder()
		s.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPost, path, nil))
		if rec.Code != 405 || rec.Header().Get("Allow") != "GET, HEAD" {
			t.Fatalf("POST %s should return 405 with Allow", path)
		}
	}
	unknown := httptest.NewRecorder()
	s.Handler().ServeHTTP(unknown, httptest.NewRequest(http.MethodGet, "/swagger/does-not-exist", nil))
	if unknown.Code != 404 {
		t.Fatal("unknown documentation asset must return 404")
	}

	if !strings.Contains(string(contracts.AuthOpenAPI()), "openapi: 3.1.0") {
		t.Fatal("OpenAPI contract was not embedded")
	}
	if strings.Contains(string(contracts.AuthOpenAPI()), "127.0.0.1:18080") {
		t.Fatal("published API spec points to visitor loopback")
	}
}
