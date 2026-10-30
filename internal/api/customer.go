package api

import (
	"errors"
	"net/http"
	"strings"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/customer"
	"github.com/google/uuid"
)

func isCustomerRoute(action string) bool {
	return action == "customer/profile" ||
		action == "customer/vehicles" || strings.HasPrefix(action, "customer/vehicles/") ||
		action == "customer/addresses" || strings.HasPrefix(action, "customer/addresses/")
}

func (s *Server) customerError(w http.ResponseWriter, id string, err error) {
	switch {
	case errors.Is(err, customer.ErrNotFound):
		s.fail(w, id, http.StatusNotFound, "NOT_FOUND", err.Error())
	case errors.Is(err, customer.ErrInvalid):
		s.fail(w, id, http.StatusUnprocessableEntity, "VALIDATION_FAILED", err.Error())
	case errors.Is(err, customer.ErrConflict):
		s.fail(w, id, http.StatusConflict, "CONFLICT", err.Error())
	case errors.Is(err, customer.ErrVersionMismatch):
		s.fail(w, id, http.StatusPreconditionFailed, "RESOURCE_VERSION_MISMATCH", err.Error())
	case errors.Is(err, customer.ErrUnauthorized):
		s.fail(w, id, http.StatusUnauthorized, "UNAUTHORIZED", err.Error())
	case errors.Is(err, customer.ErrForbidden):
		s.fail(w, id, http.StatusForbidden, "FORBIDDEN", err.Error())
	default:
		if s.Logger != nil {
			s.Logger.Error("customer operation failed", "error", err)
		}
		s.fail(w, id, http.StatusInternalServerError, "INTERNAL_ERROR", "Internal server error")
	}
}

func (s *Server) handleCustomer(w http.ResponseWriter, r *http.Request, id string, principal auth.Principal, action string) {
	accountID, err := uuid.Parse(principal.AccountID)
	if err != nil {
		s.fail(w, id, http.StatusUnauthorized, "INVALID_CREDENTIALS", "Invalid account ID")
		return
	}

	repo := customer.NewRepository(s.DB)
	svc := customer.NewService(repo)

	subPath := strings.TrimPrefix(action, "customer/")
	parts := strings.Split(strings.Trim(subPath, "/"), "/")
	resource := parts[0]

	switch resource {
	case "profile":
		switch r.Method {
		case http.MethodGet:
			profile, err := svc.GetProfile(r.Context(), accountID)
			if err != nil {
				s.customerError(w, id, err)
				return
			}
			s.ok(w, http.StatusOK, profile)

		case http.MethodPost:
			var req customer.CreateProfileRequest
			if !s.decode(w, r, id, &req) {
				return
			}
			created, err := svc.CreateProfile(r.Context(), accountID, &req)
			if err != nil {
				s.customerError(w, id, err)
				return
			}
			s.ok(w, http.StatusCreated, created)

		case http.MethodPatch, http.MethodPut:
			var req customer.UpdateProfileRequest
			if !s.decode(w, r, id, &req) {
				return
			}
			updated, err := svc.UpdateProfile(r.Context(), accountID, &req)
			if err != nil {
				s.customerError(w, id, err)
				return
			}
			s.ok(w, http.StatusOK, updated)

		default:
			s.fail(w, id, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
		}

	case "vehicles":
		if len(parts) == 1 {
			switch r.Method {
			case http.MethodGet:
				activeOnly := r.URL.Query().Get("all") != "true"
				vehicles, err := svc.ListVehicles(r.Context(), accountID, activeOnly)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, vehicles)

			case http.MethodPost:
				var req customer.CreateVehicleRequest
				if !s.decode(w, r, id, &req) {
					return
				}
				v, err := svc.CreateVehicle(r.Context(), accountID, &req)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusCreated, v)

			default:
				s.fail(w, id, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
			}
			return
		}

		if len(parts) == 2 {
			vehicleID, err := uuid.Parse(parts[1])
			if err != nil {
				s.fail(w, id, http.StatusBadRequest, "INVALID_ID", "Invalid vehicle ID format")
				return
			}

			switch r.Method {
			case http.MethodGet:
				v, err := svc.GetVehicle(r.Context(), accountID, vehicleID)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, v)

			case http.MethodPatch, http.MethodPut:
				var req customer.UpdateVehicleRequest
				if !s.decode(w, r, id, &req) {
					return
				}
				updated, err := svc.UpdateVehicle(r.Context(), accountID, vehicleID, &req)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, updated)

			case http.MethodDelete:
				err := svc.DeleteVehicle(r.Context(), accountID, vehicleID)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, map[string]string{"status": "deleted"})

			default:
				s.fail(w, id, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
			}
			return
		}
		s.fail(w, id, http.StatusNotFound, "NOT_FOUND", "Endpoint not found")

	case "addresses":
		if len(parts) == 1 {
			switch r.Method {
			case http.MethodGet:
				activeOnly := r.URL.Query().Get("all") != "true"
				addresses, err := svc.ListAddresses(r.Context(), accountID, activeOnly)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, addresses)

			case http.MethodPost:
				var req customer.CreateAddressRequest
				if !s.decode(w, r, id, &req) {
					return
				}
				addr, err := svc.CreateAddress(r.Context(), accountID, &req)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusCreated, addr)

			default:
				s.fail(w, id, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
			}
			return
		}

		if len(parts) == 2 {
			addressID, err := uuid.Parse(parts[1])
			if err != nil {
				s.fail(w, id, http.StatusBadRequest, "INVALID_ID", "Invalid address ID format")
				return
			}

			switch r.Method {
			case http.MethodGet:
				addr, err := svc.GetAddress(r.Context(), accountID, addressID)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, addr)

			case http.MethodPatch, http.MethodPut:
				var req customer.UpdateAddressRequest
				if !s.decode(w, r, id, &req) {
					return
				}
				updated, err := svc.UpdateAddress(r.Context(), accountID, addressID, &req)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, updated)

			case http.MethodDelete:
				err := svc.DeleteAddress(r.Context(), accountID, addressID)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, map[string]string{"status": "deleted"})

			default:
				s.fail(w, id, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
			}
			return
		}

		if len(parts) == 3 && parts[2] == "default" {
			addressID, err := uuid.Parse(parts[1])
			if err != nil {
				s.fail(w, id, http.StatusBadRequest, "INVALID_ID", "Invalid address ID format")
				return
			}
			if r.Method == http.MethodPost {
				err := svc.SetDefaultAddress(r.Context(), accountID, addressID)
				if err != nil {
					s.customerError(w, id, err)
					return
				}
				s.ok(w, http.StatusOK, map[string]string{"status": "default_set"})
				return
			}
			s.fail(w, id, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
			return
		}

		s.fail(w, id, http.StatusNotFound, "NOT_FOUND", "Endpoint not found")

	default:
		s.fail(w, id, http.StatusNotFound, "NOT_FOUND", "Endpoint not found")
	}
}

