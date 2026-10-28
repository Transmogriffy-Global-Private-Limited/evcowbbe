package fleet

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
	"github.com/jackc/pgx/v5"
)

// This test deliberately has no fallback to the normal application database.
// Provide a dedicated, independently provisioned PostgreSQL *_test database.
func TestIntegrationFleetInventory(t *testing.T) {
	uri := os.Getenv("TEST_DATABASE_URL")
	if uri == "" {
		t.Skip("TEST_DATABASE_URL missing; real PostgreSQL fleet tests not run")
	}
	u, err := url.Parse(uri)
	if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") || u.Hostname() == "" || strings.Count(u.Path, "/") != 1 || !strings.HasSuffix(u.Path, "_test") {
		t.Fatal("refusing non-disposable TEST_DATABASE_URL")
	}
	if production := os.Getenv("DATABASE_URL"); production != "" && production == uri {
		t.Fatal("refusing application database")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	db, err := database.Open(ctx, uri, 12)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(db.Close)
	if err = database.Migrate(ctx, db, "up"); err != nil {
		t.Fatal(err)
	}
	actor, err := security.UUID()
	if err != nil {
		t.Fatal(err)
	}
	suffix := strings.ReplaceAll(actor, "-", "")[:12]
	_, err = db.Exec(ctx, `INSERT INTO accounts(id,email,password_hash,email_verified_at) VALUES($1,$2,'test-only-hash',now())`, actor, fmt.Sprintf("fleet-%s@example.test", suffix))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		c, stop := context.WithTimeout(context.Background(), 10*time.Second)
		defer stop()
		// Remove only records tied to this random test actor; never truncate tables.
		for _, statement := range []string{
			`DELETE FROM mutation_idempotency_receipts WHERE actor_id=$1`,
			`DELETE FROM fleet_asset_audit WHERE actor_id=$1`,
			`DELETE FROM asset_maintenance_periods WHERE created_by=$1`,
			`DELETE FROM vehicle_charger_pairings WHERE created_by=$1`,
			`DELETE FROM charger_connectors WHERE created_by=$1`,
			`DELETE FROM chargers WHERE created_by=$1`,
			`DELETE FROM service_vehicles WHERE created_by=$1`,
			`DELETE FROM accounts WHERE id=$1`,
		} {
			if _, e := db.Exec(c, statement, actor); e != nil {
				t.Error("isolated test cleanup failed", e)
			}
		}
	})
	svc := Service{Pool: db}
	create := func(key string, r Resource, body []byte) json.RawMessage {
		t.Helper()
		fields, err := ValidateFields(r, body, true)
		if err != nil {
			t.Fatal(err)
		}
		out, status, err := svc.RunOnce(ctx, actor, key, "POST "+r.Table, body, func(tx pgx.Tx) (json.RawMessage, int, error) {
			result, e := svc.Create(ctx, tx, r, actor, fields, "")
			return result, 201, e
		})
		if err != nil || status != 201 {
			t.Fatalf("create %s: %v status %d", r.Table, err, status)
		}
		return out
	}
	getID := func(v json.RawMessage) string {
		t.Helper()
		var m struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal(v, &m); err != nil || m.ID == "" {
			t.Fatalf("bad asset JSON: %s", v)
		}
		return m.ID
	}
	vBody := []byte(fmt.Sprintf(`{"asset_tag":"SV-%s","make":"Tata","model":"Ace"}`, suffix))
	vehicleJSON := create("fleet-v-"+actor, vehicle, vBody)
	vehicleID := getID(vehicleJSON)
	replay, status, err := svc.RunOnce(ctx, actor, "fleet-v-"+actor, "POST "+vehicle.Table, vBody, func(tx pgx.Tx) (json.RawMessage, int, error) {
		t.Error("replay executed mutation")
		return nil, 0, ErrConflict
	})
	if err != nil || status != 201 || !bytes.Equal(replay, vehicleJSON) {
		t.Fatalf("idempotency replay failed: %v", err)
	}
	_, _, err = svc.RunOnce(ctx, actor, "fleet-v-"+actor, "POST "+vehicle.Table, []byte(`{"changed":true}`), func(tx pgx.Tx) (json.RawMessage, int, error) {
		t.Error("key reuse executed mutation")
		return nil, 0, nil
	})
	if !errors.Is(err, ErrKeyReuse) {
		t.Fatalf("key collision accepted: %v", err)
	}
	raceBody := []byte(fmt.Sprintf(`{"asset_tag":"RACE-%s","make":"Maker","model":"Parallel"}`, suffix))
	raceFields, err := ValidateFields(vehicle, raceBody, true)
	if err != nil {
		t.Fatal(err)
	}
	var calls atomic.Int32
	start := make(chan struct{})
	results := make(chan string, 2)
	problems := make(chan error, 2)
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			body, _, err := svc.RunOnce(ctx, actor, "fleet-race-"+actor, "POST "+vehicle.Table, raceBody, func(tx pgx.Tx) (json.RawMessage, int, error) {
				calls.Add(1)
				item, e := svc.Create(ctx, tx, vehicle, actor, raceFields, "")
				return item, 201, e
			})
			if err != nil {
				problems <- err
				return
			}
			var item struct {
				ID string `json:"id"`
			}
			if e := json.Unmarshal(body, &item); e != nil {
				problems <- e
				return
			}
			results <- item.ID
		}()
	}
	close(start)
	wg.Wait()
	close(results)
	close(problems)
	for e := range problems {
		t.Errorf("concurrent receipt failed: %v", e)
	}
	if calls.Load() != 1 {
		t.Fatalf("duplicate mutation callback count %d", calls.Load())
	}
	first := ""
	count := 0
	for id := range results {
		count++
		if first == "" {
			first = id
		} else if first != id {
			t.Fatal("duplicate keys returned different resource IDs")
		}
	}
	if count != 2 {
		t.Fatalf("received %d results, expected 2", count)
	}
	cBody := []byte(fmt.Sprintf(`{"asset_tag":"CH-%s","manufacturer":"Charger Maker","model":"Model X","rated_power_w":120000}`, suffix))
	chargerID := getID(create("fleet-c-"+actor, charger, cBody))
	fields, err := ValidateFields(connector, []byte(`{"connector_number":1,"connector_type":"CCS2","max_power_w":120000,"supports_ac":false,"supports_dc":true}`), true)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := svc.ConnectorCreate(ctx, tx, actor, chargerID, fields)
	if err != nil {
		tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if getID(conn) == "" {
		t.Fatal("connector missing")
	}
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	pairing, err := svc.Pair(ctx, tx, actor, PairRequest{VehicleID: vehicleID, ChargerID: chargerID, Reason: "Initial test pairing"})
	if err != nil {
		tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	pairID := getID(pairing)
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = svc.Pair(ctx, tx, actor, PairRequest{VehicleID: vehicleID, ChargerID: chargerID, Reason: "Competing test pairing"})
	tx.Rollback(ctx)
	if !errors.Is(err, ErrConflict) {
		t.Fatalf("concurrent exclusivity rule broken: %v", err)
	}
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = svc.SetActive(ctx, tx, vehicle, actor, vehicleID, "Deactivate during pairing", false, 1)
	tx.Rollback(ctx)
	if !errors.Is(err, ErrConflict) {
		t.Fatalf("paired vehicle deactivated: %v", err)
	}
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = svc.EndPair(ctx, tx, actor, pairID, "End test pairing")
	if err != nil {
		tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	result, err := svc.SetActive(ctx, tx, vehicle, actor, vehicleID, "Deactivate now unpaired", false, 1)
	if err != nil {
		tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(result, []byte(`"OUT_OF_SERVICE"`)) {
		t.Fatalf("deactivation state missing: %s", result)
	}
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = svc.SetActive(ctx, tx, vehicle, actor, vehicleID, "Stale version attempt", true, 1)
	tx.Rollback(ctx)
	if !errors.Is(err, ErrVersion) {
		t.Fatalf("stale version not rejected: %v", err)
	}
	history, err := svc.History(ctx, "VEHICLE", vehicleID)
	// Direct CREATE/DEACTIVATE plus subordinate PAIR/END are all visible.
	if err != nil || len(history) != 4 {
		t.Fatalf("history incomplete: %v events %d", err, len(history))
	}
	av, err := svc.Availability(ctx, vehicle, vehicleID)
	if err != nil || av.InventoryState != "OUT_OF_SERVICE" || av.DispatchEligibility != "NOT_EVALUATED" {
		t.Fatalf("availability invented live eligibility: %+v %v", av, err)
	}
	maintenanceStart := time.Now().UTC().Add(-time.Hour)
	maintenanceEnd := time.Now().UTC().Add(time.Hour)
	period := MaintenanceRequest{StartsAt: maintenanceStart.Format(time.RFC3339Nano), EndsAt: maintenanceEnd.Format(time.RFC3339Nano), Reason: "Isolated maintenance inspection"}
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = svc.Maintenance(ctx, tx, vehicle, actor, vehicleID, period)
	if err != nil {
		tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = svc.Maintenance(ctx, tx, vehicle, actor, vehicleID, period)
	tx.Rollback(ctx)
	if !errors.Is(err, ErrConflict) {
		t.Fatalf("overlapping maintenance accepted: %v", err)
	}
	av, err = svc.Availability(ctx, vehicle, vehicleID)
	if err != nil || !av.MaintenanceNow || av.InventoryState != "MAINTENANCE" || av.HALConnectivity != "NOT_INTEGRATED" {
		t.Fatalf("maintenance projection is not evidence-aware: %+v %v", av, err)
	}
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = svc.SetActive(ctx, tx, vehicle, actor, vehicleID, "Reactivate under maintenance", true, 2)
	tx.Rollback(ctx)
	if !errors.Is(err, ErrConflict) {
		t.Fatalf("reactivated during maintenance: %v", err)
	}
	history, err = svc.History(ctx, "VEHICLE", vehicleID)
	if err != nil || len(history) != 5 {
		t.Fatalf("maintenance event missing from asset history: %v events %d", err, len(history))
	}
}
