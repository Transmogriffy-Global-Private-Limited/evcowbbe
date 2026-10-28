package pricing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
)

// Destructive integration writes are permitted ONLY in an explicitly supplied
// disposable PostgreSQL database whose name ends with _test. No app DB fallback.
func TestIntegrationPricingLifecycle(t *testing.T) {
	uri := os.Getenv("TEST_DATABASE_URL")
	if uri == "" {
		t.Skip("TEST_DATABASE_URL not configured; real PostgreSQL test not run")
	}
	u, err := url.Parse(uri)
	if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") || u.Hostname() == "" || strings.Count(u.Path, "/") != 1 || !strings.HasSuffix(u.Path, "_test") {
		t.Fatal("refusing non-disposable pricing integration database")
	}
	if uri == os.Getenv("DATABASE_URL") {
		t.Fatal("test and application database may not be identical")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	pool, err := database.Open(ctx, uri, 12)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	if err = database.Migrate(ctx, pool, "up"); err != nil {
		t.Fatal(err)
	}
	actor, err := security.UUID()
	if err != nil {
		t.Fatal(err)
	}
	suffix := strings.ReplaceAll(actor, "-", "")[:12]
	_, err = pool.Exec(ctx, `INSERT INTO accounts(id,email,password_hash,email_verified_at) VALUES($1,$2,'pricing-test-only-hash',now())`, actor, fmt.Sprintf("pricing-%s@example.test", suffix))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cleanupCtx, done := context.WithTimeout(context.Background(), 10*time.Second)
		defer done()
		for _, stmt := range []string{
			`DELETE FROM pricing_policy_audit WHERE actor_id=$1`,
			`DELETE FROM pricing_policies WHERE created_by=$1`,
			`DELETE FROM accounts WHERE id=$1`,
		} {
			if _, e := pool.Exec(cleanupCtx, stmt, actor); e != nil {
				t.Error("pricing test cleanup failed", e)
			}
		}
	})
	catalog := Catalog{Pool: pool}
	create := func(key string, scope Scope, start, end *time.Time) (string, int64) {
		t.Helper()
		r := DraftRequest{PolicyKey: key, Version: 1, Scope: scope, StartsAt: start, EndsAt: end, Currency: "INR", Rules: baseTerms("12").Rules, Reason: "Approved for integration test only"}
		tx, e := pool.Begin(ctx)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback(ctx)
		data, e := catalog.CreateDraft(ctx, tx, actor, r)
		if e != nil {
			t.Fatal(e)
		}
		if e = tx.Commit(ctx); e != nil {
			t.Fatal(e)
		}
		var item struct {
			ID           string `json:"id"`
			StateVersion int64  `json:"state_version"`
		}
		if e = json.Unmarshal(data, &item); e != nil || item.ID == "" || item.StateVersion != 1 {
			t.Fatalf("bad pricing record %s %v", data, e)
		}
		return item.ID, item.StateVersion
	}
	publish := func(id string, version int64) error {
		tx, e := pool.Begin(ctx)
		if e != nil {
			return e
		}
		defer tx.Rollback(ctx)
		_, e = catalog.Publish(ctx, tx, id, actor, version, "Approved test publication")
		if e != nil {
			return e
		}
		return tx.Commit(ctx)
	}
	scope := Scope{"SERVICE_ZONE", "ZONE_" + suffix}
	root, version := create("ROOT_"+strings.ToUpper(suffix), scope, nil, nil)
	if err = publish(root, version); err != nil {
		t.Fatal(err)
	}
	if err = publish(root, version); !errors.Is(err, ErrVersionConflict) {
		t.Fatalf("stale publish accepted: %v", err)
	}
	_, err = catalog.Resolve(ctx, []Scope{scope}, *instant("2026-11-01T00:00:00Z"))
	if err != nil {
		t.Fatal(err)
	}
	second, _ := create("OTHER_"+strings.ToUpper(suffix), scope, nil, nil)
	if err = publish(second, 1); !errors.Is(err, ErrTemporalConflict) {
		t.Fatalf("duplicate root published: %v", err)
	}
	from, to := instant("2026-10-31T00:00:00Z"), instant("2026-11-03T00:00:00Z")
	bounded, _ := create("BOUNDED_"+strings.ToUpper(suffix), scope, from, to)
	if err = publish(bounded, 1); err != nil {
		t.Fatal(err)
	}
	got, err := catalog.Resolve(ctx, []Scope{scope}, *instant("2026-11-01T00:00:00Z"))
	if err != nil || got.Terms.PolicyID != "BOUNDED_"+strings.ToUpper(suffix) {
		t.Fatalf("bounded override did not win: %+v %v", got, err)
	}
	history, err := catalog.History(ctx, root)
	if err != nil {
		t.Fatal(err)
	}
	var events []json.RawMessage
	if err = json.Unmarshal(history, &events); err != nil || len(events) != 2 {
		t.Fatalf("audit incomplete: %s %v", history, err)
	}
	tx, err := pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	_, err = catalog.Withdraw(ctx, tx, root, actor, 2, "Withdraw root after integration test")
	if err != nil {
		tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err = catalog.Resolve(ctx, []Scope{scope}, *instant("2026-11-04T00:00:00Z")); !errors.Is(err, ErrNoApplicablePolicy) {
		t.Fatalf("missing fallback accepted: %v", err)
	}
	// Two concurrent attempts to publish a conflicting root must not both win.
	parallel := Scope{"SERVICE_ZONE", "RACE_" + suffix}
	a, _ := create("A_"+strings.ToUpper(suffix), parallel, nil, nil)
	b, _ := create("B_"+strings.ToUpper(suffix), parallel, nil, nil)
	start := make(chan struct{})
	results := make(chan error, 2)
	var wg sync.WaitGroup
	for _, id := range []string{a, b} {
		wg.Add(1)
		go func(id string) { defer wg.Done(); <-start; results <- publish(id, 1) }(id)
	}
	close(start)
	wg.Wait()
	close(results)
	wins, losses := 0, 0
	for e := range results {
		if e == nil {
			wins++
		} else if errors.Is(e, ErrTemporalConflict) {
			losses++
		} else {
			t.Errorf("unexpected concurrent publish error: %v", e)
		}
	}
	if wins != 1 || losses != 1 {
		t.Fatalf("scope serialization winners=%d rejected=%d", wins, losses)
	}
	var gotCount int
	if err = pool.QueryRow(ctx, `SELECT count(*) FROM pricing_policies WHERE scope_kind=$1 AND scope_key=$2 AND lifecycle='PUBLISHED'`, parallel.Kind, parallel.Key).Scan(&gotCount); err != nil || gotCount != 1 {
		t.Fatalf("conflict persisted: %d %v", gotCount, err)
	}
}
