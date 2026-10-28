package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/adminrbac"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/fleet"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/pricing"
	"github.com/jackc/pgx/v5"
)

func isPricingRoute(action string) bool {
	return action == "pricing/policies" || strings.HasPrefix(action, "pricing/policies/")
}

func (s *Server) pricingError(w http.ResponseWriter, id string, err error) {
	switch {
	case errors.Is(err, pricing.ErrInvalidPolicy):
		s.fail(w, id, 422, "VALIDATION_FAILED", "Invalid pricing policy or request")
	case errors.Is(err, pricing.ErrNotFound):
		s.fail(w, id, 404, "NOT_FOUND", "Pricing policy not found")
	case errors.Is(err, pricing.ErrVersionConflict):
		s.fail(w, id, 412, "RESOURCE_VERSION_MISMATCH", "Pricing state changed; reload before retry")
	case errors.Is(err, pricing.ErrTemporalConflict):
		s.fail(w, id, 409, "PRICING_TEMPORAL_CONFLICT", "Conflicting effective-date pricing policy")
	case errors.Is(err, pricing.ErrStateConflict), errors.Is(err, fleet.ErrConflict):
		s.fail(w, id, 409, "CONFLICT", "Pricing operation conflicts with current state")
	case errors.Is(err, fleet.ErrKeyReuse):
		s.fail(w, id, 409, "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST", "Idempotency key reused")
	default:
		if s.Logger != nil {
			s.Logger.Error("pricing catalog operation failed", "type", "internal")
		}
		s.fail(w, id, 500, "INTERNAL_ERROR", "Pricing catalog unavailable")
	}
}

func (s *Server) pricingRequire(w http.ResponseWriter, r *http.Request, id, actor, capability string) bool {
	err := (adminrbac.Service{Pool: s.DB}).Require(r.Context(), actor, capability)
	if err == nil {
		return true
	}
	if errors.Is(err, adminrbac.ErrForbidden) {
		s.rbacError(w, id, err)
	} else {
		s.pricingError(w, id, err)
	}
	return false
}

func pricingETag(w http.ResponseWriter, raw json.RawMessage) {
	var state struct {
		Version int64 `json:"state_version"`
	}
	if json.Unmarshal(raw, &state) == nil && state.Version > 0 {
		w.Header().Set("ETag", "\""+strconv.FormatInt(state.Version, 10)+"\"")
	}
}

func (s *Server) pricingMutation(w http.ResponseWriter, r *http.Request, id, actor string, raw json.RawMessage, run func(pgx.Tx) (json.RawMessage, int, error)) {
	values := r.Header.Values("Idempotency-Key")
	if len(values) != 1 || values[0] == "" {
		s.fail(w, id, 428, "IDEMPOTENCY_KEY_REQUIRED", "Provide exactly one unique Idempotency-Key")
		return
	}
	out, status, err := (fleet.Service{Pool: s.DB}).RunOnce(r.Context(), actor, values[0], r.Method+" "+r.URL.Path, raw, run)
	if err != nil {
		s.pricingError(w, id, err)
		return
	}
	pricingETag(w, out)
	s.ok(w, status, out)
}

func (s *Server) handlePricing(w http.ResponseWriter, r *http.Request, id string, p auth.Principal, path string) {
	catalog := pricing.Catalog{Pool: s.DB}
	parts := strings.Split(path, "/")
	if len(parts) < 2 || parts[0] != "pricing" || parts[1] != "policies" {
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	if len(parts) == 2 {
		switch r.Method {
		case http.MethodGet:
			if !s.pricingRequire(w, r, id, p.AccountID, "pricing.read") {
				return
			}
			for key, values := range r.URL.Query() {
				if (key != "status" && key != "limit") || len(values) != 1 {
					s.fail(w, id, 422, "VALIDATION_FAILED", "Invalid filters")
					return
				}
			}
			limit := 50
			if v := r.URL.Query().Get("limit"); v != "" {
				parsed, err := strconv.Atoi(v)
				if err != nil || parsed < 1 || parsed > 100 {
					s.fail(w, id, 422, "VALIDATION_FAILED", "Invalid limit")
					return
				}
				limit = parsed
			}
			data, err := catalog.List(r.Context(), r.URL.Query().Get("status"), limit)
			if err != nil {
				s.pricingError(w, id, err)
				return
			}
			s.ok(w, 200, data)
		case http.MethodPost:
			if !s.pricingRequire(w, r, id, p.AccountID, "pricing.manage") {
				return
			}
			raw, ok := s.fleetPayload(w, r, id)
			if !ok {
				return
			}
			var draft pricing.DraftRequest
			if pricing.StrictJSON(raw, &draft) != nil || draft.Validate() != nil {
				s.pricingError(w, id, pricing.ErrInvalidPolicy)
				return
			}
			s.pricingMutation(w, r, id, p.AccountID, raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
				data, err := catalog.CreateDraft(r.Context(), tx, p.AccountID, draft)
				return data, 201, err
			})
		default:
			s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		}
		return
	}
	if !uuidFormat(parts[2]) || len(parts) > 4 {
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	target := parts[2]
	if len(parts) == 3 && r.Method == http.MethodGet {
		if !s.pricingRequire(w, r, id, p.AccountID, "pricing.read") {
			return
		}
		data, err := catalog.Get(r.Context(), target)
		if err != nil {
			s.pricingError(w, id, err)
			return
		}
		pricingETag(w, data)
		s.ok(w, 200, data)
		return
	}
	if len(parts) == 4 && parts[3] == "history" && r.Method == http.MethodGet {
		if !s.pricingRequire(w, r, id, p.AccountID, "pricing.read") || !s.pricingRequire(w, r, id, p.AccountID, "audit.read") {
			return
		}
		data, err := catalog.History(r.Context(), target)
		if err != nil {
			s.pricingError(w, id, err)
			return
		}
		s.ok(w, 200, data)
		return
	}
	if len(parts) != 4 || r.Method != http.MethodPost || (parts[3] != "publish" && parts[3] != "withdraw") {
		s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		return
	}
	if !s.pricingRequire(w, r, id, p.AccountID, "pricing.manage") {
		return
	}
	version, status := fleetPrecondition(r)
	if status != 0 {
		s.fail(w, id, status, "IF_MATCH_REQUIRED", "Supply the current strong ETag in If-Match")
		return
	}
	raw, ok := s.fleetPayload(w, r, id)
	if !ok {
		return
	}
	var transition pricing.TransitionRequest
	if pricing.StrictJSON(raw, &transition) != nil || !fleet.ValidReason(transition.Reason) {
		s.pricingError(w, id, pricing.ErrInvalidPolicy)
		return
	}
	s.pricingMutation(w, r, id, p.AccountID, raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
		var data json.RawMessage
		var err error
		if parts[3] == "publish" {
			data, err = catalog.Publish(r.Context(), tx, target, p.AccountID, version, transition.Reason)
		}
		if parts[3] == "withdraw" {
			data, err = catalog.Withdraw(r.Context(), tx, target, p.AccountID, version, transition.Reason)
		}
		return data, 200, err
	})
}
