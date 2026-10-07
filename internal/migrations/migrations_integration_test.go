package migrations

import (
	"context"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

func TestManagerWithDisposablePostgreSQL(t *testing.T) {
	databaseURL := os.Getenv("TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("TEST_DATABASE_URL not set; disposable PostgreSQL migration integration test skipped")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("open disposable PostgreSQL: %v", err)
	}
	defer pool.Close()

	manager := &Manager{
		pool:        pool,
		ledgerTable: "evcowbbe_test_schema_migrations",
		lockID:      advisoryLockID + 1,
	}
	cleanupManagerTables(t, ctx, pool, manager.ledgerTable)
	t.Cleanup(func() { cleanupManagerTables(t, context.Background(), pool, manager.ledgerTable) })

	manager.migrations = []Migration{{
		Version:  1,
		Name:     "create_probe",
		SQL:      `CREATE TABLE public.evcowbbe_migration_probe (id INTEGER PRIMARY KEY);`,
		Checksum: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
	}}
	if err := manager.Apply(ctx, ""); err != nil {
		t.Fatalf("Apply returned error: %v", err)
	}
	if err := manager.CheckReady(ctx); err != nil {
		t.Fatalf("CheckReady returned error: %v", err)
	}

	changed := *manager
	changed.migrations = []Migration{{
		Version:  1,
		Name:     "create_probe",
		SQL:      manager.migrations[0].SQL,
		Checksum: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
	}}
	if err := changed.Verify(ctx); !errors.Is(err, ErrChecksumMismatch) {
		t.Fatalf("expected checksum mismatch, got %v", err)
	}
}

func TestManagerRollsBackFailedMigrationWithDisposablePostgreSQL(t *testing.T) {
	databaseURL := os.Getenv("TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("TEST_DATABASE_URL not set; disposable PostgreSQL migration integration test skipped")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("open disposable PostgreSQL: %v", err)
	}
	defer pool.Close()

	manager := &Manager{
		pool:        pool,
		ledgerTable: "evcowbbe_test_rollback_migrations",
		lockID:      advisoryLockID + 2,
		migrations: []Migration{{
			Version:  1,
			Name:     "fail_after_create",
			SQL:      `CREATE TABLE public.evcowbbe_migration_rollback_probe (id INTEGER PRIMARY KEY); SELECT missing_function();`,
			Checksum: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
		}},
	}
	cleanupManagerTables(t, ctx, pool, manager.ledgerTable)
	_, _ = pool.Exec(ctx, `DROP TABLE IF EXISTS public.evcowbbe_migration_rollback_probe`)
	t.Cleanup(func() {
		cleanupManagerTables(t, context.Background(), pool, manager.ledgerTable)
		_, _ = pool.Exec(context.Background(), `DROP TABLE IF EXISTS public.evcowbbe_migration_rollback_probe`)
	})

	if err := manager.Apply(ctx, ""); err == nil {
		t.Fatal("expected failed migration")
	}

	var probeExists bool
	if err := pool.QueryRow(ctx, `SELECT to_regclass('public.evcowbbe_migration_rollback_probe') IS NOT NULL`).Scan(&probeExists); err != nil {
		t.Fatalf("check rollback probe: %v", err)
	}
	if probeExists {
		t.Fatal("failed transactional migration left its table behind")
	}

	var recorded int
	if err := pool.QueryRow(ctx, fmt.Sprintf(`SELECT count(*) FROM %s`, manager.quotedLedgerTable())).Scan(&recorded); err != nil {
		t.Fatalf("read migration ledger: %v", err)
	}
	if recorded != 0 {
		t.Fatalf("expected no recorded migration after rollback, got %d", recorded)
	}
}

func TestMigrationLockSerializesConcurrentAccessWithDisposablePostgreSQL(t *testing.T) {
	databaseURL := os.Getenv("TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("TEST_DATABASE_URL not set; disposable PostgreSQL migration integration test skipped")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("open disposable PostgreSQL: %v", err)
	}
	defer pool.Close()

	manager := &Manager{
		pool:        pool,
		ledgerTable: "evcowbbe_test_lock_migrations",
		lockID:      advisoryLockID + 3,
	}
	cleanupManagerTables(t, ctx, pool, manager.ledgerTable)
	t.Cleanup(func() { cleanupManagerTables(t, context.Background(), pool, manager.ledgerTable) })

	entered := make(chan struct{})
	release := make(chan struct{})
	completed := make(chan error, 1)
	go func() {
		completed <- manager.withLock(ctx, false, func(*pgxpool.Conn) error {
			close(entered)
			<-release
			return nil
		})
	}()
	<-entered

	if err := manager.CheckReady(ctx); !errors.Is(err, ErrLockBusy) {
		t.Fatalf("expected readiness to reject a held migration lock, got %v", err)
	}
	close(release)
	if err := <-completed; err != nil {
		t.Fatalf("lock holder returned error: %v", err)
	}
}

func cleanupManagerTables(t *testing.T, ctx context.Context, pool *pgxpool.Pool, ledgerTable string) {
	t.Helper()
	if _, err := pool.Exec(ctx, fmt.Sprintf(`DROP TABLE IF EXISTS %s`, (&Manager{ledgerTable: ledgerTable}).quotedLedgerTable())); err != nil {
		t.Fatalf("drop test migration ledger: %v", err)
	}
	_, _ = pool.Exec(ctx, `DROP TABLE IF EXISTS public.evcowbbe_migration_probe`)
}
