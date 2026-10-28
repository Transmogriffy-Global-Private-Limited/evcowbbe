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
	"github.com/jackc/pgx/v5"
)

func isFleetRoute(action string) bool {
	for _, prefix := range []string{"vehicles", "chargers", "pairings"} {
		if action == prefix || strings.HasPrefix(action, prefix+"/") {
			return true
		}
	}
	return false
}

func (s *Server) fleetError(w http.ResponseWriter, id string, err error) {
	switch {
	case errors.Is(err, fleet.ErrInvalid):
		s.fail(w, id, 422, "VALIDATION_FAILED", "Invalid fleet request")
	case errors.Is(err, fleet.ErrNotFound):
		s.fail(w, id, 404, "NOT_FOUND", "Asset not found")
	case errors.Is(err, fleet.ErrConflict):
		s.fail(w, id, 409, "CONFLICT", "Fleet state conflicts with this operation")
	case errors.Is(err, fleet.ErrVersion):
		s.fail(w, id, 412, "RESOURCE_VERSION_MISMATCH", "Asset version changed; reload and retry")
	case errors.Is(err, fleet.ErrKeyReuse):
		s.fail(w, id, 409, "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST", "Idempotency key belongs to another request")
	default:
		if s.Logger != nil {
			s.Logger.Error("fleet operation failed", "type", "internal")
		}
		s.fail(w, id, 500, "INTERNAL_ERROR", "Fleet operation unavailable")
	}
}

func (s *Server) requireFleet(w http.ResponseWriter, r *http.Request, id, actor, capability string) bool {
	err := (adminrbac.Service{Pool: s.DB}).Require(r.Context(), actor, capability)
	if err == nil {
		return true
	}
	if errors.Is(err, adminrbac.ErrForbidden) {
		s.rbacError(w, id, err)
	} else {
		s.fleetError(w, id, err)
	}
	return false
}

func (s *Server) fleetPayload(w http.ResponseWriter, r *http.Request, id string) ([]byte, bool) {
	if !s.mutationBody(w, r, id, r.Method) {
		return nil, false
	}
	var raw json.RawMessage
	if !s.decode(w, r, id, &raw) {
		return nil, false
	}
	if len(raw) == 0 || raw[0] != '{' {
		s.fail(w, id, 422, "VALIDATION_FAILED", "Expected JSON object")
		return nil, false
	}
	return raw, true
}

func fleetPrecondition(r *http.Request) (int64, int) {
	values := r.Header.Values("If-Match")
	if len(values) == 0 {
		return 0, 428
	}
	if len(values) != 1 {
		return 0, 400
	}
	v := strings.TrimSpace(values[0])
	if len(v) < 3 || v[0] != '"' || v[len(v)-1] != '"' {
		return 0, 400
	}
	number, err := strconv.ParseInt(v[1:len(v)-1], 10, 64)
	if err != nil || number < 1 {
		return 0, 400
	}
	return number, 0
}

func fleetETag(w http.ResponseWriter, data json.RawMessage) {
	var v struct {
		Version int64 `json:"version"`
	}
	if json.Unmarshal(data, &v) == nil && v.Version > 0 {
		w.Header().Set("ETag", "\""+strconv.FormatInt(v.Version, 10)+"\"")
	}
}

func (s *Server) fleetMutation(w http.ResponseWriter, r *http.Request, id, actor, op string, body []byte, run func(pgx.Tx) (json.RawMessage, int, error)) {
	values := r.Header.Values("Idempotency-Key")
	if len(values) != 1 || values[0] == "" {
		s.fail(w, id, 428, "IDEMPOTENCY_KEY_REQUIRED", "Supply one unique Idempotency-Key header")
		return
	}
	out, status, err := (fleet.Service{Pool: s.DB}).RunOnce(r.Context(), actor, values[0], op, body, run)
	if err != nil {
		s.fleetError(w, id, err)
		return
	}
	fleetETag(w, out)
	s.ok(w, status, out)
}

func parseFleetFilter(r *http.Request) (fleet.Filter, error) {
	q := r.URL.Query()
	allowed := map[string]bool{"limit": true, "after": true, "status": true, "active": true, "q": true}
	for k, v := range q {
		if !allowed[k] || len(v) != 1 {
			return fleet.Filter{}, fleet.ErrInvalid
		}
	}
	var f fleet.Filter
	f.After = q.Get("after")
	if f.After != "" && !uuidFormat(f.After) {
		return f, fleet.ErrInvalid
	}
	f.Status = q.Get("status")
	f.Query = strings.TrimSpace(q.Get("q"))
	if v := q.Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 100 {
			return f, fleet.ErrInvalid
		}
		f.Limit = n
	}
	if v := q.Get("active"); v != "" {
		b, err := strconv.ParseBool(v)
		if err != nil {
			return f, fleet.ErrInvalid
		}
		f.Active = &b
	}
	return f, nil
}

func (s *Server) handleFleet(w http.ResponseWriter, r *http.Request, id string, p auth.Principal, path string) {
	svc := fleet.Service{Pool: s.DB}
	parts := strings.Split(path, "/")
	if len(parts) == 0 {
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	if parts[0] == "pairings" {
		s.handleFleetPairings(w, r, id, p, parts, svc)
		return
	}
	resource, ok := fleet.Kind(parts[0])
	if !ok {
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	if len(parts) == 1 {
		switch r.Method {
		case http.MethodGet:
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.read") {
				return
			}
			f, err := parseFleetFilter(r)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			page, err := svc.List(r.Context(), resource, f)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			s.ok(w, 200, page)
		case http.MethodPost:
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.manage") {
				return
			}
			raw, ok := s.fleetPayload(w, r, id)
			if !ok {
				return
			}
			fields, err := fleet.ValidateFields(resource, raw, true)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			s.fleetMutation(w, r, id, p.AccountID, r.Method+" "+r.URL.Path, raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
				data, err := svc.Create(r.Context(), tx, resource, p.AccountID, fields, "")
				return data, 201, err
			})
		default:
			s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		}
		return
	}
	if !uuidFormat(parts[1]) {
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	target := parts[1]
	if len(parts) == 2 {
		switch r.Method {
		case http.MethodGet:
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.read") {
				return
			}
			data, err := svc.Get(r.Context(), resource, target)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			fleetETag(w, data)
			s.ok(w, 200, data)
		case http.MethodPatch:
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.manage") {
				return
			}
			version, status := fleetPrecondition(r)
			if status != 0 {
				s.fleetPreconditionError(w, id, status)
				return
			}
			raw, ok := s.fleetPayload(w, r, id)
			if !ok {
				return
			}
			fields, err := fleet.ValidateFields(resource, raw, false)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			s.fleetMutation(w, r, id, p.AccountID, r.Method+" "+r.URL.Path+" "+strconv.FormatInt(version, 10), raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
				data, err := svc.Patch(r.Context(), tx, resource, p.AccountID, target, version, fields, "")
				return data, 200, err
			})
		default:
			s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		}
		return
	}
	if len(parts) == 3 {
		switch parts[2] {
		case "history":
			if r.Method != http.MethodGet {
				s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
				return
			}
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.read") || !s.requireFleet(w, r, id, p.AccountID, "audit.read") {
				return
			}
			_, err := svc.Get(r.Context(), resource, target)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			events, err := svc.History(r.Context(), resource.AuditKind, target)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			s.ok(w, 200, map[string]any{"items": events})
			return
		case "availability":
			if r.Method != http.MethodGet {
				s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
				return
			}
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.read") {
				return
			}
			data, err := svc.Availability(r.Context(), resource, target)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			s.ok(w, 200, data)
			return
		case "deactivate", "reactivate":
			if r.Method != http.MethodPost {
				s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
				return
			}
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.manage") {
				return
			}
			version, status := fleetPrecondition(r)
			if status != 0 {
				s.fleetPreconditionError(w, id, status)
				return
			}
			raw, ok := s.fleetPayload(w, r, id)
			if !ok {
				return
			}
			var req struct {
				Reason string `json:"reason"`
			}
			if json.Unmarshal(raw, &req) != nil || !jsonKeysOnly(raw, "reason") || !fleet.ValidReason(req.Reason) {
				s.fleetError(w, id, fleet.ErrInvalid)
				return
			}
			active := parts[2] == "reactivate"
			s.fleetMutation(w, r, id, p.AccountID, r.Method+" "+r.URL.Path+" "+strconv.FormatInt(version, 10), raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
				data, err := svc.SetActive(r.Context(), tx, resource, p.AccountID, target, req.Reason, active, version)
				return data, 200, err
			})
			return
		case "maintenance":
			if r.Method != http.MethodPost {
				s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
				return
			}
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.maintenance") {
				return
			}
			raw, ok := s.fleetPayload(w, r, id)
			if !ok {
				return
			}
			var req fleet.MaintenanceRequest
			if json.Unmarshal(raw, &req) != nil || !jsonKeysOnly(raw, "starts_at", "ends_at", "reason") {
				s.fleetError(w, id, fleet.ErrInvalid)
				return
			}
			s.fleetMutation(w, r, id, p.AccountID, r.Method+" "+r.URL.Path, raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
				data, err := svc.Maintenance(r.Context(), tx, resource, p.AccountID, target, req)
				return data, 201, err
			})
			return
		case "connectors":
			if parts[0] != "chargers" {
				break
			}
			s.handleFleetConnectors(w, r, id, p, target, "", resource, svc)
			return
		}
	}
	if len(parts) == 4 && parts[0] == "chargers" && parts[2] == "connectors" && uuidFormat(parts[3]) {
		s.handleFleetConnectors(w, r, id, p, target, parts[3], resource, svc)
		return
	}
	s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
}

func (s *Server) fleetPreconditionError(w http.ResponseWriter, id string, status int) {
	if status == 428 {
		s.fail(w, id, 428, "PRECONDITION_REQUIRED", "Supply If-Match from a current resource ETag")
	} else {
		s.fail(w, id, 400, "VALIDATION_FAILED", "Invalid If-Match version")
	}
}

func jsonKeysOnly(raw []byte, allowed ...string) bool {
	var values map[string]json.RawMessage
	if json.Unmarshal(raw, &values) != nil || values == nil {
		return false
	}
	names := map[string]bool{}
	for _, v := range allowed {
		names[v] = true
	}
	for k := range values {
		if !names[k] {
			return false
		}
	}
	return true
}

func (s *Server) handleFleetConnectors(w http.ResponseWriter, r *http.Request, id string, p auth.Principal, chargerID, connectorID string, _ fleet.Resource, svc fleet.Service) {
	if connectorID == "" {
		switch r.Method {
		case http.MethodGet:
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.read") {
				return
			}
			out, err := svc.Connectors(r.Context(), chargerID)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			s.ok(w, 200, map[string]any{"items": out})
		case http.MethodPost:
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.manage") {
				return
			}
			raw, ok := s.fleetPayload(w, r, id)
			if !ok {
				return
			}
			fields, err := fleet.ValidateConnector(raw, true)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			s.fleetMutation(w, r, id, p.AccountID, r.Method+" "+r.URL.Path, raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
				data, err := svc.ConnectorCreate(r.Context(), tx, p.AccountID, chargerID, fields)
				return data, 201, err
			})
		default:
			s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		}
		return
	}
	if r.Method != http.MethodPatch {
		s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		return
	}
	if !s.requireFleet(w, r, id, p.AccountID, "fleet.manage") {
		return
	}
	version, status := fleetPrecondition(r)
	if status != 0 {
		s.fleetPreconditionError(w, id, status)
		return
	}
	raw, ok := s.fleetPayload(w, r, id)
	if !ok {
		return
	}
	fields, err := fleet.ValidateConnector(raw, false)
	if err != nil {
		s.fleetError(w, id, err)
		return
	}
	s.fleetMutation(w, r, id, p.AccountID, r.Method+" "+r.URL.Path+" "+strconv.FormatInt(version, 10), raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
		data, err := svc.ConnectorPatch(r.Context(), tx, p.AccountID, chargerID, connectorID, version, fields)
		return data, 200, err
	})
}

func (s *Server) handleFleetPairings(w http.ResponseWriter, r *http.Request, id string, p auth.Principal, parts []string, svc fleet.Service) {
	if len(parts) == 1 {
		switch r.Method {
		case http.MethodGet:
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.read") {
				return
			}
			q := r.URL.Query()
			for k, v := range q {
				if len(v) != 1 || (k != "vehicle_id" && k != "charger_id" && k != "status" && k != "limit") {
					s.fleetError(w, id, fleet.ErrInvalid)
					return
				}
			}
			for _, v := range []string{q.Get("vehicle_id"), q.Get("charger_id")} {
				if v != "" && !uuidFormat(v) {
					s.fleetError(w, id, fleet.ErrInvalid)
					return
				}
			}
			limit := 50
			if v := q.Get("limit"); v != "" {
				var err error
				limit, err = strconv.Atoi(v)
				if err != nil || limit < 1 || limit > 100 {
					s.fleetError(w, id, fleet.ErrInvalid)
					return
				}
			}
			out, err := svc.Pairings(r.Context(), q.Get("vehicle_id"), q.Get("charger_id"), q.Get("status"), limit)
			if err != nil {
				s.fleetError(w, id, err)
				return
			}
			s.ok(w, 200, map[string]any{"items": out})
		case http.MethodPost:
			if !s.requireFleet(w, r, id, p.AccountID, "fleet.manage") {
				return
			}
			raw, ok := s.fleetPayload(w, r, id)
			if !ok {
				return
			}
			var req fleet.PairRequest
			if json.Unmarshal(raw, &req) != nil || !jsonKeysOnly(raw, "vehicle_id", "charger_id", "reason") || !uuidFormat(req.VehicleID) || !uuidFormat(req.ChargerID) {
				s.fleetError(w, id, fleet.ErrInvalid)
				return
			}
			s.fleetMutation(w, r, id, p.AccountID, r.Method+" "+r.URL.Path, raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
				data, err := svc.Pair(r.Context(), tx, p.AccountID, req)
				return data, 201, err
			})
		default:
			s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		}
		return
	}
	if len(parts) == 3 && uuidFormat(parts[1]) && parts[2] == "end" {
		if r.Method != http.MethodPost {
			s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
			return
		}
		if !s.requireFleet(w, r, id, p.AccountID, "fleet.manage") {
			return
		}
		raw, ok := s.fleetPayload(w, r, id)
		if !ok {
			return
		}
		var req struct {
			Reason string `json:"reason"`
		}
		if json.Unmarshal(raw, &req) != nil || !jsonKeysOnly(raw, "reason") || !fleet.ValidReason(req.Reason) {
			s.fleetError(w, id, fleet.ErrInvalid)
			return
		}
		s.fleetMutation(w, r, id, p.AccountID, r.Method+" "+r.URL.Path, raw, func(tx pgx.Tx) (json.RawMessage, int, error) {
			data, err := svc.EndPair(r.Context(), tx, p.AccountID, parts[1], req.Reason)
			return data, 200, err
		})
		return
	}
	s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
}
