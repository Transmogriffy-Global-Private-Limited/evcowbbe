package api

import (
	"net/http"
	"strings"
)

// These six development origins are explicitly authorized for the staging API.
// Origin matching is intentionally exact: CORS is a browser read policy, not
// authentication, a trusted-IP list, or a substitute for Admin RBAC.
var corsDevelopmentOrigins = map[string]struct{}{
	"http://localhost:5173": {},
	"http://localhost:8080": {},
	"http://localhost:8081": {},
	"http://127.0.0.1:5173": {},
	"http://127.0.0.1:8080": {},
	"http://127.0.0.1:8081": {},
}

var corsMethods = map[string]struct{}{
	http.MethodGet: {}, http.MethodHead: {}, http.MethodPost: {},
	http.MethodPut: {}, http.MethodPatch: {}, http.MethodDelete: {},
}

var corsRequestHeaders = map[string]struct{}{
	"accept": {}, "authorization": {}, "cache-control": {},
	"content-type": {}, "idempotency-key": {}, "if-match": {},
	"pragma": {}, "x-request-id": {}, "x-requested-with": {},
}

const (
	corsAllowedMethods = "GET, HEAD, POST, PUT, PATCH, DELETE"
	corsAllowedHeaders = "Accept, Authorization, Cache-Control, Content-Type, Idempotency-Key, If-Match, Pragma, X-Request-ID, X-Requested-With"
)

func corsEndpoint(path string) bool {
	return strings.HasPrefix(path, "/api/v1/") ||
		strings.HasPrefix(path, "/health/") ||
		path == "/version" || path == "/openapi.yaml"
}

func corsHeadersValid(requested string) bool {
	if strings.TrimSpace(requested) == "" {
		return true
	}
	for _, part := range strings.Split(requested, ",") {
		header := strings.ToLower(strings.TrimSpace(part))
		if _, ok := corsRequestHeaders[header]; !ok {
			return false
		}
	}
	return true
}

// cors accepts valid browser preflights without invoking authentication or
// rate limiting, and annotates actual responses (including errors). The
// method/headers check is deliberately separate from per-endpoint validation:
// the real request still goes through all existing auth and routing checks.
func (s *Server) cors(w http.ResponseWriter, r *http.Request, requestID string) bool {
	origin := r.Header.Get("Origin")
	if origin == "" || !corsEndpoint(r.URL.Path) {
		return false
	}
	w.Header().Add("Vary", "Origin")
	_, allowed := corsDevelopmentOrigins[origin]
	if r.Method != http.MethodOptions {
		if allowed {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Expose-Headers", "ETag, X-Request-ID, Retry-After")
		}
		return false
	}
	w.Header().Add("Vary", "Access-Control-Request-Method")
	w.Header().Add("Vary", "Access-Control-Request-Headers")
	if !allowed {
		s.fail(w, requestID, http.StatusForbidden, "CORS_ORIGIN_FORBIDDEN", "Origin is not allowed")
		return true
	}
	method := r.Header.Get("Access-Control-Request-Method")
	if _, ok := corsMethods[method]; !ok {
		s.fail(w, requestID, http.StatusBadRequest, "CORS_METHOD_INVALID", "Preflight method is not allowed")
		return true
	}
	if !corsHeadersValid(r.Header.Get("Access-Control-Request-Headers")) {
		s.fail(w, requestID, http.StatusBadRequest, "CORS_HEADERS_INVALID", "Preflight headers are not allowed")
		return true
	}
	w.Header().Set("Access-Control-Allow-Origin", origin)
	w.Header().Set("Access-Control-Allow-Methods", corsAllowedMethods)
	w.Header().Set("Access-Control-Allow-Headers", corsAllowedHeaders)
	w.Header().Set("Access-Control-Max-Age", "600")
	w.WriteHeader(http.StatusNoContent)
	return true
}
