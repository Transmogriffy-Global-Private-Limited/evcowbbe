package api

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
)

func TestHTTPContractWithoutExternalServices(t *testing.T) {
	s := &Server{C: config.Config{Env: "development"}}
	cases := []struct {
		name, method, path, contentType, body string
		status                            int
		code                              string
	}{
		{"liveness", "GET", "/health/live", "", "", 200, ""},
		{"version", "GET", "/version", "", "", 200, ""},
		{"unknown route", "GET", "/definitely-missing", "", "", 404, "NOT_FOUND"},
		{"options disallowed", "OPTIONS", "/api/v1/admin/auth/login", "", "", 405, "METHOD_NOT_ALLOWED"},
		{"no public admin registration", "POST", "/api/v1/admin/auth/register", "application/json", "{}", 403, "FORBIDDEN"},
		{"login wrong method", "GET", "/api/v1/app/auth/login", "", "", 405, "METHOD_NOT_ALLOWED"},
		{"login wrong media type", "POST", "/api/v1/app/auth/login", "text/plain", "{}", 415, "UNSUPPORTED_MEDIA_TYPE"},
		{"invalid JSON", "POST", "/api/v1/app/auth/login", "application/json", "{", 400, "VALIDATION_FAILED"},
		{"unknown JSON field", "POST", "/api/v1/app/auth/login", "application/json", "{\"email\":\"e@example.test\",\"password\":\"value\",\"admin\":true}", 400, "VALIDATION_FAILED"},
		{"trailing JSON object", "POST", "/api/v1/app/auth/login", "application/json", "{} {}", 400, "VALIDATION_FAILED"},
		{"oversized body", "POST", "/api/v1/app/auth/login", "application/json", "{\"email\":\"" + strings.Repeat("x", 33000) + "\"}", 400, "VALIDATION_FAILED"},
		{"no bearer", "GET", "/api/v1/admin/me", "", "", 401, "INVALID_CREDENTIALS"},
		{"invalid bearer", "GET", "/api/v1/driver/me", "", "", 401, "INVALID_CREDENTIALS"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(tc.method, tc.path, bytes.NewBufferString(tc.body))
			if tc.contentType != "" {
				req.Header.Set("Content-Type", tc.contentType)
			}
			if tc.name == "invalid bearer" {
				req.Header.Set("Authorization", "Bearer invalid")
			}
			rec := httptest.NewRecorder()
			s.Handler().ServeHTTP(rec, req)
			if rec.Code != tc.status {
				t.Fatalf("status: got %d, want %d", rec.Code, tc.status)
			}
			if rec.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("missing no-store response policy")
			}
			if rec.Header().Get("X-Request-ID") == "" {
				t.Fatal("missing request identifier")
			}
			var envelope struct {
				Data  json.RawMessage `json:"data"`
				Error struct {
					Code string `json:"code"`
				} `json:"error"`
			}
			if err := json.Unmarshal(rec.Body.Bytes(), &envelope); err != nil {
				t.Fatalf("response not JSON: %v", err)
			}
			if tc.code != "" && envelope.Error.Code != tc.code {
				t.Fatalf("error code: got %q, want %q", envelope.Error.Code, tc.code)
			}
			if tc.code == "" && len(envelope.Data) == 0 {
				t.Fatal("successful response missing data envelope")
			}
		})
	}
}

func TestHTTPPublicRequestIdentifierAndSecurityHeaders(t *testing.T) {
	id := "12345678-1234-1234-1234-123456789abc"
	s := &Server{C: config.Config{Env: "production"}}
	req := httptest.NewRequest("GET", "/health/live", nil)
	req.Header.Set("X-Request-ID", id)
	rec := httptest.NewRecorder()
	s.Handler().ServeHTTP(rec, req)
	if rec.Header().Get("X-Request-ID") != id {
		t.Fatal("valid request ID was not retained")
	}
	if !strings.HasPrefix(rec.Header().Get("Strict-Transport-Security"), "max-age=") {
		t.Fatal("missing production HSTS header")
	}
	if rec.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Fatal("missing nosniff header")
	}
}
