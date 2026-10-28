package fleet

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// SetActive changes only master lifecycle status, never a live DutySession,
// HAL connection, or a previously recorded physical pairing.
func (s Service) SetActive(ctx context.Context, tx pgx.Tx, r Resource, actor, id, reason string, active bool, version int64) (json.RawMessage, error) {
	if !ValidReason(reason) {
		return nil, ErrInvalid
	}
	var current int64
	var existing bool
	var status string
	err := tx.QueryRow(ctx, "SELECT version,is_active,operational_status FROM "+r.Table+" WHERE id=$1 FOR UPDATE", id).Scan(&current, &existing, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if current != version {
		return nil, ErrVersion
	}
	if status == "RETIRED" {
		return nil, ErrConflict
	}
	if existing == active {
		return fetch(ctx, tx, r.Table, id)
	}
	if !active {
		var paired bool
		column := "vehicle_id"
		if r.Table == "chargers" {
			column = "charger_id"
		}
		err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM vehicle_charger_pairings WHERE "+column+"=$1 AND status='ACTIVE')", id).Scan(&paired)
		if err != nil {
			return nil, err
		}
		if paired {
			return nil, ErrConflict
		}
	} else {
		var maintenance bool
		col := "vehicle_id"
		if r.Table == "chargers" {
			col = "charger_id"
		}
		err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM asset_maintenance_periods WHERE "+col+"=$1 AND starts_at<=now() AND ends_at>now())", id).Scan(&maintenance)
		if err != nil {
			return nil, err
		}
		if maintenance {
			return nil, ErrConflict
		}
	}
	before, err := fetch(ctx, tx, r.Table, id)
	if err != nil {
		return nil, err
	}
	next := "OUT_OF_SERVICE"
	if active {
		next = "AVAILABLE"
	}
	_, err = tx.Exec(ctx, "UPDATE "+r.Table+" SET is_active=$2,operational_status=$3,version=version+1,updated_by=$4,updated_at=now() WHERE id=$1", id, active, next, actor)
	if err != nil {
		return nil, IntegrityError(err)
	}
	after, err := fetch(ctx, tx, r.Table, id)
	if err != nil {
		return nil, err
	}
	action := "DEACTIVATE"
	if active {
		action = "REACTIVATE"
	}
	if err = audit(ctx, tx, actor, r.AuditKind, id, action, current+1, reason, before, after); err != nil {
		return nil, err
	}
	return after, nil
}

type Availability struct {
	AssetID             string `json:"asset_id"`
	InventoryState      string `json:"inventory_state"`
	ActivePairings      int    `json:"active_pairings"`
	MaintenanceNow      bool   `json:"maintenance_now"`
	DispatchEligibility string `json:"dispatch_eligibility"`
	HALConnectivity     string `json:"hal_connectivity"`
}

func (s Service) Availability(ctx context.Context, r Resource, id string) (Availability, error) {
	var isActive bool
	var status string
	err := s.Pool.QueryRow(ctx, "SELECT is_active,operational_status FROM "+r.Table+" WHERE id=$1", id).Scan(&isActive, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return Availability{}, ErrNotFound
	}
	if err != nil {
		return Availability{}, err
	}
	col := "vehicle_id"
	if r.Table == "chargers" {
		col = "charger_id"
	}
	var maintenance bool
	var count int
	err = s.Pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM asset_maintenance_periods WHERE "+col+"=$1 AND starts_at<=now() AND ends_at>now())", id).Scan(&maintenance)
	if err != nil {
		return Availability{}, err
	}
	err = s.Pool.QueryRow(ctx, "SELECT COUNT(*) FROM vehicle_charger_pairings WHERE "+col+"=$1 AND status='ACTIVE'", id).Scan(&count)
	if err != nil {
		return Availability{}, err
	}
	state := status
	if maintenance {
		state = "MAINTENANCE"
	} else if !isActive && status != "RETIRED" {
		state = "OUT_OF_SERVICE"
	}
	return Availability{AssetID: id, InventoryState: state, ActivePairings: count, MaintenanceNow: maintenance, DispatchEligibility: "NOT_EVALUATED", HALConnectivity: "NOT_INTEGRATED"}, nil
}

func (s Service) Connectors(ctx context.Context, chargerID string) ([]json.RawMessage, error) {
	var present bool
	if err := s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM chargers WHERE id=$1)`, chargerID).Scan(&present); err != nil {
		return nil, err
	}
	if !present {
		return nil, ErrNotFound
	}
	rows, err := s.Pool.Query(ctx, `SELECT to_jsonb(c) FROM charger_connectors c WHERE charger_id=$1 ORDER BY connector_number LIMIT 100`, chargerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []json.RawMessage{}
	for rows.Next() {
		var row []byte
		if err = rows.Scan(&row); err != nil {
			return nil, err
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

func (s Service) ConnectorCreate(ctx context.Context, tx pgx.Tx, actor, chargerID string, fields map[string]any) (json.RawMessage, error) {
	var active bool
	var state string
	err := tx.QueryRow(ctx, `SELECT is_active,operational_status FROM chargers WHERE id=$1 FOR UPDATE`, chargerID).Scan(&active, &state)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if !active || state != "AVAILABLE" {
		return nil, ErrConflict
	}
	if ac, ok := fields["supports_ac"].(bool); ok {
		if dc, ok := fields["supports_dc"].(bool); ok && !ac && !dc {
			return nil, ErrInvalid
		}
	}
	return s.Create(ctx, tx, connector, actor, fields, chargerID)
}

func (s Service) ConnectorPatch(ctx context.Context, tx pgx.Tx, actor, chargerID, id string, version int64, fields map[string]any) (json.RawMessage, error) {
	// Lock parent first, so concurrent deactivation and connector edits serialize.
	var active bool
	err := tx.QueryRow(ctx, `SELECT is_active FROM chargers WHERE id=$1 FOR UPDATE`, chargerID).Scan(&active)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if !active {
		return nil, ErrConflict
	}
	var currentAC, currentDC bool
	err = tx.QueryRow(ctx, `SELECT supports_ac,supports_dc FROM charger_connectors WHERE id=$1 AND charger_id=$2 FOR UPDATE`, id, chargerID).Scan(&currentAC, &currentDC)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if v, ok := fields["supports_ac"].(bool); ok {
		currentAC = v
	}
	if v, ok := fields["supports_dc"].(bool); ok {
		currentDC = v
	}
	if !currentAC && !currentDC {
		return nil, ErrInvalid
	}
	return s.Patch(ctx, tx, connector, actor, id, version, fields, chargerID)
}

type PairRequest struct {
	VehicleID string `json:"vehicle_id"`
	ChargerID string `json:"charger_id"`
	Reason    string `json:"reason"`
}

func (s Service) Pair(ctx context.Context, tx pgx.Tx, actor string, req PairRequest) (json.RawMessage, error) {
	if !ValidReason(req.Reason) {
		return nil, ErrInvalid
	}
	var vehicleActive, chargerActive bool
	var vehicleState, chargerState string
	// Global lock hierarchy: ServiceVehicle, then Charger.
	err := tx.QueryRow(ctx, `SELECT is_active,operational_status FROM service_vehicles WHERE id=$1 FOR UPDATE`, req.VehicleID).Scan(&vehicleActive, &vehicleState)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	err = tx.QueryRow(ctx, `SELECT is_active,operational_status FROM chargers WHERE id=$1 FOR UPDATE`, req.ChargerID).Scan(&chargerActive, &chargerState)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if !vehicleActive || !chargerActive || vehicleState != "AVAILABLE" || chargerState != "AVAILABLE" {
		return nil, ErrConflict
	}
	var blocked bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM asset_maintenance_periods
 WHERE starts_at<=now() AND ends_at>now() AND (vehicle_id=$1 OR charger_id=$2))`, req.VehicleID, req.ChargerID).Scan(&blocked)
	if err != nil {
		return nil, err
	}
	if blocked {
		return nil, ErrConflict
	}
	id, err := uuid()
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO vehicle_charger_pairings(id,vehicle_id,charger_id,effective_from,status,source,created_by,reason)
 VALUES($1,$2,$3,now(),'ACTIVE','ADMIN',$4,$5)`, id, req.VehicleID, req.ChargerID, actor, req.Reason)
	if err != nil {
		return nil, IntegrityError(err)
	}
	after, err := fetch(ctx, tx, "vehicle_charger_pairings", id)
	if err != nil {
		return nil, err
	}
	if err = audit(ctx, tx, actor, "PAIRING", id, "PAIR", 1, req.Reason, nil, after); err != nil {
		return nil, err
	}
	return after, nil
}

func (s Service) EndPair(ctx context.Context, tx pgx.Tx, actor, id, reason string) (json.RawMessage, error) {
	if !ValidReason(reason) {
		return nil, ErrInvalid
	}
	var vehicleID, chargerID string
	err := tx.QueryRow(ctx, `SELECT vehicle_id,charger_id FROM vehicle_charger_pairings WHERE id=$1`, id).Scan(&vehicleID, &chargerID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	// Lock master assets in the same order as Pair and future duty claims.
	var guard string
	err = tx.QueryRow(ctx, `SELECT id FROM service_vehicles WHERE id=$1 FOR UPDATE`, vehicleID).Scan(&guard)
	if err != nil {
		return nil, err
	}
	err = tx.QueryRow(ctx, `SELECT id FROM chargers WHERE id=$1 FOR UPDATE`, chargerID).Scan(&guard)
	if err != nil {
		return nil, err
	}
	var state string
	err = tx.QueryRow(ctx, `SELECT status FROM vehicle_charger_pairings WHERE id=$1 FOR UPDATE`, id).Scan(&state)
	if err != nil {
		return nil, err
	}
	if state != "ACTIVE" {
		return nil, ErrConflict
	}
	before, err := fetch(ctx, tx, "vehicle_charger_pairings", id)
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE vehicle_charger_pairings SET status='ENDED',effective_to=GREATEST(now(),effective_from+interval '1 microsecond'),ended_by=$2,end_reason=$3 WHERE id=$1`, id, actor, reason)
	if err != nil {
		return nil, IntegrityError(err)
	}
	after, err := fetch(ctx, tx, "vehicle_charger_pairings", id)
	if err != nil {
		return nil, err
	}
	if err = audit(ctx, tx, actor, "PAIRING", id, "END", 2, reason, before, after); err != nil {
		return nil, err
	}
	return after, nil
}

func (s Service) Pairings(ctx context.Context, vehicleID, chargerID, status string, limit int) ([]json.RawMessage, error) {
	if limit == 0 {
		limit = 50
	}
	if limit < 1 || limit > 100 {
		return nil, ErrInvalid
	}
	if status != "" && status != "ACTIVE" && status != "ENDED" && status != "INVALIDATED" {
		return nil, ErrInvalid
	}
	rows, err := s.Pool.Query(ctx, `SELECT to_jsonb(p) FROM vehicle_charger_pairings p WHERE ($1::uuid IS NULL OR vehicle_id=$1::uuid)
 AND ($2::uuid IS NULL OR charger_id=$2::uuid) AND ($3::text='' OR status=$3) ORDER BY effective_from DESC,id LIMIT $4`, nullableID(vehicleID), nullableID(chargerID), status, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []json.RawMessage{}
	for rows.Next() {
		var row []byte
		if err = rows.Scan(&row); err != nil {
			return nil, err
		}
		out = append(out, row)
	}
	return out, rows.Err()
}
func nullableID(id string) any {
	if id == "" {
		return nil
	}
	return id
}

type MaintenanceRequest struct {
	StartsAt string `json:"starts_at"`
	EndsAt   string `json:"ends_at"`
	Reason   string `json:"reason"`
}

func (s Service) Maintenance(ctx context.Context, tx pgx.Tx, r Resource, actor, id string, req MaintenanceRequest) (json.RawMessage, error) {
	if !ValidReason(req.Reason) {
		return nil, ErrInvalid
	}
	start, err := time.Parse(time.RFC3339Nano, req.StartsAt)
	if err != nil {
		return nil, ErrInvalid
	}
	end, err := time.Parse(time.RFC3339Nano, req.EndsAt)
	if err != nil || !end.After(start) {
		return nil, ErrInvalid
	}
	var status string
	err = tx.QueryRow(ctx, "SELECT operational_status FROM "+r.Table+" WHERE id=$1 FOR UPDATE", id).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if status == "RETIRED" {
		return nil, ErrConflict
	}
	col := "vehicle_id"
	if r.Table == "chargers" {
		col = "charger_id"
	}
	var overlap bool
	err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM asset_maintenance_periods WHERE "+col+"=$1 AND starts_at<$3 AND ends_at>$2)", id, start, end).Scan(&overlap)
	if err != nil {
		return nil, err
	}
	if overlap {
		return nil, ErrConflict
	}
	maintenanceID, err := uuid()
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, "INSERT INTO asset_maintenance_periods(id,"+col+",starts_at,ends_at,reason,created_by) VALUES($1,$2,$3,$4,$5,$6)", maintenanceID, id, start, end, req.Reason, actor)
	if err != nil {
		return nil, IntegrityError(err)
	}
	after, err := fetch(ctx, tx, "asset_maintenance_periods", maintenanceID)
	if err != nil {
		return nil, err
	}
	if err = audit(ctx, tx, actor, "MAINTENANCE", maintenanceID, "SCHEDULE", 1, req.Reason, nil, after); err != nil {
		return nil, err
	}
	return after, nil
}
