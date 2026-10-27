package user

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
)

// Authenticator validates plane-specific bearer access tokens.
type Authenticator interface {
	Authenticate(ctx context.Context, bearer, plane string) (auth.Principal, error)
}

// Router dispatches customer vehicle HTTP requests under the app plane.
type Router struct {
	Service *Service
	Auth    Authenticator
}

// NewRouter constructs a new Router instance.
func NewRouter(service *Service, authenticator Authenticator) *Router {
	return &Router{
		Service: service,
		Auth:    authenticator,
	}
}

type responseEnvelope struct {
	Data any `json:"data"`
}

type errorEnvelope struct {
	Error struct {
		Code      string         `json:"code"`
		Message   string         `json:"message"`
		RequestID string         `json:"request_id"`
		Details   map[string]any `json:"details"`
	} `json:"error"`
}

func (rt *Router) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	reqID, _ := security.UUID()
	if incoming := r.Header.Get("X-Request-ID"); len(incoming) == 36 {
		reqID = incoming
	}

	w.Header().Set("X-Request-ID", reqID)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("X-Frame-Options", "DENY")

	// 1. Authenticate bearer token from the Authorization header.
	authHeader := r.Header.Get("Authorization")
	if !strings.HasPrefix(authHeader, "Bearer ") || strings.Count(authHeader, " ") != 1 {
		rt.fail(w, reqID, http.StatusUnauthorized, "UNAUTHORIZED", "Missing or invalid Authorization header")
		return
	}
	token := strings.TrimPrefix(authHeader, "Bearer ")

	principal, err := rt.Auth.Authenticate(r.Context(), token, "app")
	if err != nil {
		if errors.Is(err, auth.ErrForbidden) {
			rt.fail(w, reqID, http.StatusForbidden, "FORBIDDEN", "Access not permitted for this principal")
			return
		}
		rt.fail(w, reqID, http.StatusUnauthorized, "UNAUTHORIZED", "Invalid or expired access token")
		return
	}

	// 2. Dispatch the authenticated request.
	rt.DispatchWithPrincipal(w, r, principal, reqID)
}

// DispatchWithPrincipal allows dispatching when the caller (e.g. main server) has already authenticated the principal.
func (rt *Router) DispatchWithPrincipal(w http.ResponseWriter, r *http.Request, principal auth.Principal, reqID string) {
	if reqID == "" {
		reqID, _ = security.UUID()
	}

	// Clean path relative to vehicles endpoint.
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/app/")
	path = strings.TrimPrefix(path, "vehicles")
	path = strings.Trim(path, "/")

	if path == "" {
		switch r.Method {
		case http.MethodGet:
			rt.handleList(w, r, principal, reqID)
		case http.MethodPost:
			rt.handleCreate(w, r, principal, reqID)
		default:
			w.Header().Set("Allow", "GET, POST")
			rt.fail(w, reqID, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
		}
		return
	}

	parts := strings.Split(path, "/")
	vehicleID := parts[0]

	if len(parts) == 1 {
		switch r.Method {
		case http.MethodGet:
			rt.handleGet(w, r, principal, vehicleID, reqID)
		case http.MethodPut, http.MethodPatch:
			rt.handleUpdate(w, r, principal, vehicleID, reqID)
		case http.MethodDelete:
			rt.handleDelete(w, r, principal, vehicleID, reqID)
		default:
			w.Header().Set("Allow", "GET, PUT, PATCH, DELETE")
			rt.fail(w, reqID, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
		}
		return
	}

	if len(parts) == 2 && parts[1] == "default" {
		if r.Method == http.MethodPost {
			rt.handleSetDefault(w, r, principal, vehicleID, reqID)
			return
		}
		w.Header().Set("Allow", "POST")
		rt.fail(w, reqID, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
		return
	}

	rt.fail(w, reqID, http.StatusNotFound, "NOT_FOUND", "Endpoint not found")
}

func (rt *Router) handleCreate(w http.ResponseWriter, r *http.Request, principal auth.Principal, reqID string) {
	var req CreateVehicleRequest
	if !rt.decodeJSON(w, r, reqID, &req) {
		return
	}

	res, err := rt.Service.Create(r.Context(), principal.AccountID, req)
	if err != nil {
		rt.handleError(w, reqID, err)
		return
	}

	rt.ok(w, http.StatusCreated, res)
}

func (rt *Router) handleList(w http.ResponseWriter, r *http.Request, principal auth.Principal, reqID string) {
	list, err := rt.Service.List(r.Context(), principal.AccountID)
	if err != nil {
		rt.handleError(w, reqID, err)
		return
	}

	rt.ok(w, http.StatusOK, list)
}

func (rt *Router) handleGet(w http.ResponseWriter, r *http.Request, principal auth.Principal, vehicleID, reqID string) {
	res, err := rt.Service.Get(r.Context(), principal.AccountID, vehicleID)
	if err != nil {
		rt.handleError(w, reqID, err)
		return
	}

	rt.ok(w, http.StatusOK, res)
}

func (rt *Router) handleUpdate(w http.ResponseWriter, r *http.Request, principal auth.Principal, vehicleID, reqID string) {
	var req UpdateVehicleRequest
	if !rt.decodeJSON(w, r, reqID, &req) {
		return
	}

	res, err := rt.Service.Update(r.Context(), principal.AccountID, vehicleID, req)
	if err != nil {
		rt.handleError(w, reqID, err)
		return
	}

	rt.ok(w, http.StatusOK, res)
}

func (rt *Router) handleDelete(w http.ResponseWriter, r *http.Request, principal auth.Principal, vehicleID, reqID string) {
	if err := rt.Service.Delete(r.Context(), principal.AccountID, vehicleID); err != nil {
		rt.handleError(w, reqID, err)
		return
	}

	rt.ok(w, http.StatusOK, map[string]string{"message": "vehicle deleted successfully"})
}

func (rt *Router) handleSetDefault(w http.ResponseWriter, r *http.Request, principal auth.Principal, vehicleID, reqID string) {
	if err := rt.Service.SetDefault(r.Context(), principal.AccountID, vehicleID); err != nil {
		rt.handleError(w, reqID, err)
		return
	}

	rt.ok(w, http.StatusOK, map[string]string{"message": "vehicle marked as default"})
}

func (rt *Router) decodeJSON(w http.ResponseWriter, r *http.Request, reqID string, target any) bool {
	contentType := strings.ToLower(strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0]))
	if contentType != "application/json" {
		rt.fail(w, reqID, http.StatusUnsupportedMediaType, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")
		return false
	}

	reader := http.MaxBytesReader(w, r.Body, 32768)
	defer reader.Close()

	d := json.NewDecoder(reader)
	d.DisallowUnknownFields()
	if err := d.Decode(target); err != nil {
		rt.fail(w, reqID, http.StatusBadRequest, "VALIDATION_FAILED", "Invalid JSON request body")
		return false
	}

	var rest any
	if err := d.Decode(&rest); err != io.EOF {
		rt.fail(w, reqID, http.StatusBadRequest, "VALIDATION_FAILED", "Only one JSON object permitted")
		return false
	}

	return true
}

func (rt *Router) ok(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(responseEnvelope{Data: data})
}

func (rt *Router) fail(w http.ResponseWriter, reqID string, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	var o errorEnvelope
	o.Error.Code = code
	o.Error.Message = message
	o.Error.RequestID = reqID
	o.Error.Details = map[string]any{}
	_ = json.NewEncoder(w).Encode(o)
}

func (rt *Router) handleError(w http.ResponseWriter, reqID string, err error) {
	switch {
	case errors.Is(err, ErrNotFound):
		rt.fail(w, reqID, http.StatusNotFound, "NOT_FOUND", "Vehicle not found")
	case errors.Is(err, ErrConflict):
		rt.fail(w, reqID, http.StatusConflict, "CONFLICT", "A vehicle with this registration number already exists")
	case errors.Is(err, ErrUnauthorized):
		rt.fail(w, reqID, http.StatusUnauthorized, "UNAUTHORIZED", "Unauthorized access")
	case errors.Is(err, ErrDependency):
		rt.fail(w, reqID, http.StatusServiceUnavailable, "DEPENDENCY_UNAVAILABLE", "Database or service unavailable")
	default:
		rt.fail(w, reqID, http.StatusUnprocessableEntity, "VALIDATION_FAILED", err.Error())
	}
}

