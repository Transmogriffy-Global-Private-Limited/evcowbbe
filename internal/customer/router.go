// Package customer provides HTTP routing and handlers for customer APIs.
package customer

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
	"github.com/google/uuid"
)

// Router handles HTTP requests for customer profile, vehicle, and address endpoints.
type Router struct {
	Service     *Service
	AuthService *auth.Service
}

func NewRouter(service *Service, authService *auth.Service) *Router {
	return &Router{
		Service:     service,
		AuthService: authService,
	}
}

type successResponse struct {
	Data any `json:"data"`
}

type errorDetail struct {
	Code      string         `json:"code"`
	Message   string         `json:"message"`
	RequestID string         `json:"request_id"`
	Details   map[string]any `json:"details,omitempty"`
}

type errorResponse struct {
	Error errorDetail `json:"error"`
}

func (rt *Router) requestID(r *http.Request) string {
	if incoming := r.Header.Get("X-Request-ID"); len(incoming) == 36 {
		if _, err := uuid.Parse(incoming); err == nil {
			return incoming
		}
	}
	id, err := security.UUID()
	if err != nil {
		return uuid.New().String()
	}
	return id
}

func (rt *Router) sendJSON(w http.ResponseWriter, status int, body any, reqID string) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Request-ID", reqID)
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func (rt *Router) sendError(w http.ResponseWriter, status int, code, message, reqID string) {
	rt.sendJSON(w, status, errorResponse{
		Error: errorDetail{
			Code:      code,
			Message:   message,
			RequestID: reqID,
		},
	}, reqID)
}

func (rt *Router) sendDomainError(w http.ResponseWriter, err error, reqID string) {
	switch {
	case errors.Is(err, ErrNotFound):
		rt.sendError(w, http.StatusNotFound, "NOT_FOUND", err.Error(), reqID)
	case errors.Is(err, ErrInvalid):
		rt.sendError(w, http.StatusUnprocessableEntity, "VALIDATION_FAILED", err.Error(), reqID)
	case errors.Is(err, ErrConflict):
		rt.sendError(w, http.StatusConflict, "CONFLICT", err.Error(), reqID)
	case errors.Is(err, ErrVersionMismatch):
		rt.sendError(w, http.StatusPreconditionFailed, "RESOURCE_VERSION_MISMATCH", err.Error(), reqID)
	case errors.Is(err, ErrUnauthorized):
		rt.sendError(w, http.StatusUnauthorized, "UNAUTHORIZED", err.Error(), reqID)
	case errors.Is(err, ErrForbidden):
		rt.sendError(w, http.StatusForbidden, "FORBIDDEN", err.Error(), reqID)
	default:
		rt.sendError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Internal server error", reqID)
	}
}

func (rt *Router) authenticate(r *http.Request) (uuid.UUID, error) {
	bearer := r.Header.Get("Authorization")
	if !strings.HasPrefix(bearer, "Bearer ") || strings.Count(bearer, " ") != 1 {
		return uuid.Nil, ErrUnauthorized
	}
	token := strings.TrimPrefix(bearer, "Bearer ")
	if rt.AuthService == nil {
		return uuid.Nil, ErrUnauthorized
	}
	principal, err := rt.AuthService.Authenticate(r.Context(), token, "app")
	if err != nil {
		return uuid.Nil, ErrUnauthorized
	}
	accountID, err := uuid.Parse(principal.AccountID)
	if err != nil {
		return uuid.Nil, ErrUnauthorized
	}
	return accountID, nil
}

func (rt *Router) decodeBody(r *http.Request, target any) error {
	defer r.Body.Close()
	decoder := json.NewDecoder(io.LimitReader(r.Body, 32768))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("%w: invalid JSON payload", ErrInvalid)
	}
	return nil
}

// ServeHTTP routes customer endpoints under either /api/v1/app/customer/ or /api/v1/app/.
func (rt *Router) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	reqID := rt.requestID(r)
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/app/")
	path = strings.TrimPrefix(path, "customer/")

	accountID, err := rt.authenticate(r)
	if err != nil {
		rt.sendDomainError(w, err, reqID)
		return
	}

	parts := strings.Split(strings.Trim(path, "/"), "/")
	resource := parts[0]

	switch resource {
	case "profile":
		rt.handleProfile(w, r, accountID, reqID)
	case "vehicles":
		rt.handleVehicles(w, r, accountID, parts[1:], reqID)
	case "addresses":
		rt.handleAddresses(w, r, accountID, parts[1:], reqID)
	default:
		rt.sendError(w, http.StatusNotFound, "NOT_FOUND", "Endpoint not found", reqID)
	}
}

func (rt *Router) handleProfile(w http.ResponseWriter, r *http.Request, accountID uuid.UUID, reqID string) {
	switch r.Method {
	case http.MethodGet:
		profile, err := rt.Service.GetProfile(r.Context(), accountID)
		if err != nil {
			rt.sendDomainError(w, err, reqID)
			return
		}
		rt.sendJSON(w, http.StatusOK, successResponse{Data: profile}, reqID)

	case http.MethodPost:
		var req CreateProfileRequest
		if err := rt.decodeBody(r, &req); err != nil {
			rt.sendDomainError(w, err, reqID)
			return
		}
		created, err := rt.Service.CreateProfile(r.Context(), accountID, &req)
		if err != nil {
			rt.sendDomainError(w, err, reqID)
			return
		}
		rt.sendJSON(w, http.StatusCreated, successResponse{Data: created}, reqID)

	case http.MethodPatch, http.MethodPut:
		var req UpdateProfileRequest
		if err := rt.decodeBody(r, &req); err != nil {
			rt.sendDomainError(w, err, reqID)
			return
		}
		updated, err := rt.Service.UpdateProfile(r.Context(), accountID, &req)
		if err != nil {
			rt.sendDomainError(w, err, reqID)
			return
		}
		rt.sendJSON(w, http.StatusOK, successResponse{Data: updated}, reqID)

	default:
		rt.sendError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed", reqID)
	}
}

func (rt *Router) handleVehicles(w http.ResponseWriter, r *http.Request, accountID uuid.UUID, subParts []string, reqID string) {
	if len(subParts) == 0 {
		switch r.Method {
		case http.MethodGet:
			activeOnly := r.URL.Query().Get("all") != "true"
			vehicles, err := rt.Service.ListVehicles(r.Context(), accountID, activeOnly)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: vehicles}, reqID)

		case http.MethodPost:
			var req CreateVehicleRequest
			if err := rt.decodeBody(r, &req); err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			v, err := rt.Service.CreateVehicle(r.Context(), accountID, &req)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusCreated, successResponse{Data: v}, reqID)

		default:
			rt.sendError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed", reqID)
		}
		return
	}

	if len(subParts) == 1 {
		vehicleID, err := uuid.Parse(subParts[0])
		if err != nil {
			rt.sendError(w, http.StatusBadRequest, "INVALID_ID", "Invalid vehicle ID format", reqID)
			return
		}

		switch r.Method {
		case http.MethodGet:
			v, err := rt.Service.GetVehicle(r.Context(), accountID, vehicleID)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: v}, reqID)

		case http.MethodPatch, http.MethodPut:
			var req UpdateVehicleRequest
			if err := rt.decodeBody(r, &req); err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			updated, err := rt.Service.UpdateVehicle(r.Context(), accountID, vehicleID, &req)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: updated}, reqID)

		case http.MethodDelete:
			err := rt.Service.DeleteVehicle(r.Context(), accountID, vehicleID)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: map[string]string{"status": "deleted"}}, reqID)

		default:
			rt.sendError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed", reqID)
		}
		return
	}

	rt.sendError(w, http.StatusNotFound, "NOT_FOUND", "Endpoint not found", reqID)
}

func (rt *Router) handleAddresses(w http.ResponseWriter, r *http.Request, accountID uuid.UUID, subParts []string, reqID string) {
	if len(subParts) == 0 {
		switch r.Method {
		case http.MethodGet:
			activeOnly := r.URL.Query().Get("all") != "true"
			addresses, err := rt.Service.ListAddresses(r.Context(), accountID, activeOnly)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: addresses}, reqID)

		case http.MethodPost:
			var req CreateAddressRequest
			if err := rt.decodeBody(r, &req); err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			addr, err := rt.Service.CreateAddress(r.Context(), accountID, &req)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusCreated, successResponse{Data: addr}, reqID)

		default:
			rt.sendError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed", reqID)
		}
		return
	}

	if len(subParts) == 1 {
		addressID, err := uuid.Parse(subParts[0])
		if err != nil {
			rt.sendError(w, http.StatusBadRequest, "INVALID_ID", "Invalid address ID format", reqID)
			return
		}

		switch r.Method {
		case http.MethodGet:
			addr, err := rt.Service.GetAddress(r.Context(), accountID, addressID)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: addr}, reqID)

		case http.MethodPatch, http.MethodPut:
			var req UpdateAddressRequest
			if err := rt.decodeBody(r, &req); err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			updated, err := rt.Service.UpdateAddress(r.Context(), accountID, addressID, &req)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: updated}, reqID)

		case http.MethodDelete:
			err := rt.Service.DeleteAddress(r.Context(), accountID, addressID)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: map[string]string{"status": "deleted"}}, reqID)

		default:
			rt.sendError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed", reqID)
		}
		return
	}

	if len(subParts) == 2 && subParts[1] == "default" {
		addressID, err := uuid.Parse(subParts[0])
		if err != nil {
			rt.sendError(w, http.StatusBadRequest, "INVALID_ID", "Invalid address ID format", reqID)
			return
		}

		if r.Method == http.MethodPost {
			err := rt.Service.SetDefaultAddress(r.Context(), accountID, addressID)
			if err != nil {
				rt.sendDomainError(w, err, reqID)
				return
			}
			rt.sendJSON(w, http.StatusOK, successResponse{Data: map[string]string{"status": "default_set"}}, reqID)
			return
		}

		rt.sendError(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed", reqID)
		return
	}

	rt.sendError(w, http.StatusNotFound, "NOT_FOUND", "Endpoint not found", reqID)
}
