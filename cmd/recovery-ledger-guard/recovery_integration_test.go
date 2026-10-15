//go:build recovery_integration

// Real PostgreSQL interaction tests. The environment gate deliberately refuses
// any database other than the short-lived GitHub Actions loopback fixture.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

const fixtureDatabase = "evcowbbe_recovery_ci"

func validateIsolatedDSN(raw string, authorized bool) error {
	if !authorized {
		return errors.New("isolation gate is not enabled")
	}
	u, err := url.Parse(raw)
	if err != nil || u == nil || u.Scheme != "postgres" ||
		u.Host != "127.0.0.1:5432" || u.Path != "/"+fixtureDatabase ||
		u.User == nil || u.User.Username() != "recovery_ci" {
		return errors.New("refusing non-loopback or non-disposable PostgreSQL fixture")
	}
	password, ok := u.User.Password()
	if !ok || password != "recovery_ci" || u.Query().Get("sslmode") != "disable" {
		return errors.New("disposable PostgreSQL credential/transport contract mismatch")
	}
	if u.Fragment != "" || u.RawQuery != "sslmode=disable" {
		return errors.New("unexpected PostgreSQL fixture query parameters")
	}
	return nil
}

func isolatedDSN(t *testing.T) string {
	t.Helper()
	raw := os.Getenv("RECOVERY_TEST_POSTGRES_URL")
	if err := validateIsolatedDSN(raw, os.Getenv("RECOVERY_TEST_ISOLATED") == "1"); err != nil {
		t.Fatal(err)
	}
	return raw
}

func fixtureConnection(t *testing.T, dsn string) *pgx.Conn {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal("disposable PostgreSQL fixture unavailable:", err)
	}
	t.Cleanup(func() { _ = conn.Close(context.Background()) })
	return conn
}

func prepareEmptyLedger(t *testing.T, conn *pgx.Conn) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	_, err := conn.Exec(ctx, `CREATE TABLE IF NOT EXISTS public.evcowbbe_schema_migrations (
		version bigint PRIMARY KEY, name text NOT NULL,
		checksum text NOT NULL, min_compatible_binary_version bigint
	)`)
	if err != nil {
		t.Fatal("cannot initialize disposable migration ledger:", err)
	}
	_, err = conn.Exec(ctx, "TRUNCATE TABLE public.evcowbbe_schema_migrations")
	if err != nil {
		t.Fatal("cannot reset disposable migration ledger:", err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_, _ = conn.Exec(ctx, "TRUNCATE TABLE public.evcowbbe_schema_migrations")
	})
}

type guardProcess struct {
	cmd   *exec.Cmd
	stdin io.WriteCloser
	lines *bufio.Reader
	done  chan struct{}
	err   error
}

func compileGuard(t *testing.T) string {
	t.Helper()
	binary := filepath.Join(t.TempDir(), "recovery-ledger-guard")
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "go", "build", "-trimpath", "-o", binary, ".")
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("cannot build review-target guard: %v (%s)", err, output)
	}
	return binary
}

func startGuard(t *testing.T, binary, dsn string) *guardProcess {
	t.Helper()
	cmd := exec.Command(binary)
	cmd.Env = []string{"DATABASE_URL=" + dsn, "LANG=C", "PATH=/usr/bin:/bin"}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	// Do not echo stderr: it must not leak DSNs or arbitrary backend errors.
	cmd.Stderr = io.Discard
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	g := &guardProcess{cmd: cmd, stdin: stdin, lines: bufio.NewReader(stdout), done: make(chan struct{})}
	go func() { g.err = cmd.Wait(); close(g.done) }()
	t.Cleanup(func() {
		select {
		case <-g.done:
		default:
			_ = cmd.Process.Kill()
		}
		select {
		case <-g.done:
		case <-time.After(5 * time.Second):
			t.Error("guard did not terminate during fixture cleanup")
		}
	})
	return g
}

func guardLine(t *testing.T, g *guardProcess) reply {
	t.Helper()
	res := make(chan []byte, 1)
	go func() {
		line, _ := g.lines.ReadBytes('\n')
		res <- line
	}()
	select {
	case raw := <-res:
		if len(raw) == 0 || len(raw) > 1024 {
			t.Fatal("guard did not emit a bounded acknowledgement")
		}
		var result reply
		if err := json.Unmarshal(raw, &result); err != nil || result.State != "held" ||
			!result.LedgerPresent || result.Applied != 0 || result.Fingerprint != expectedEmptySHA256 {
			t.Fatal("guard did not prove pinned ledger")
		}
		return result
	case <-time.After(8 * time.Second):
		t.Fatal("timed out waiting for PostgreSQL guard acknowledgement")
	}
	return reply{}
}

func guardStops(t *testing.T, g *guardProcess) {
	t.Helper()
	select {
	case <-g.done:
		err := g.err
		if err == nil {
			t.Fatal("guard unexpectedly exited successfully without a release instruction")
		}
	case <-time.After(8 * time.Second):
		t.Fatal("guard failed to terminate after losing its fence")
	}
}

func assertLockUnavailable(t *testing.T, conn *pgx.Conn, statement string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	if _, err := conn.Exec(ctx, "SET lock_timeout = '250ms'"); err != nil {
		t.Fatal(err)
	}
	_, err := conn.Exec(ctx, statement)
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "55P03" {
		t.Fatalf("expected PostgreSQL lock_not_available; got %v", err)
	}
}

func TestDisposablePostgresWriterFenceAndRelease(t *testing.T) {
	dsn := isolatedDSN(t)
	owner := fixtureConnection(t, dsn)
	prepareEmptyLedger(t, owner)
	writer := fixtureConnection(t, dsn)
	guard := startGuard(t, compileGuard(t), dsn)
	guardLine(t, guard)

	// Verify the migration advisory lock and SQL-table writer lock separately.
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	var granted bool
	if err := writer.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", migrationAdvisoryLockID).Scan(&granted); err != nil || granted {
		t.Fatal("migration advisory lock was not held exclusively")
	}
	assertLockUnavailable(t, writer, `INSERT INTO public.evcowbbe_schema_migrations
		(version, name, checksum) VALUES (1, 'fixture', 'checksum')`)
	assertLockUnavailable(t, writer, "ALTER TABLE public.evcowbbe_schema_migrations ADD COLUMN unwanted integer")
	if _, err := io.WriteString(guard.stdin, "ping\n"); err != nil {
		t.Fatal(err)
	}
	guardLine(t, guard)
	if _, err := io.WriteString(guard.stdin, "release\n"); err != nil {
		t.Fatal(err)
	}
	_ = guard.stdin.Close()
	select {
	case <-guard.done:
		if guard.err != nil {
			t.Fatal("guard failed to exit successfully following release", guard.err)
		}
	case <-time.After(8 * time.Second):
		t.Fatal("guard did not release writer lock promptly")
	}
	postRelease, stop := context.WithTimeout(context.Background(), 5*time.Second)
	defer stop()
	if _, err := writer.Exec(postRelease, `INSERT INTO public.evcowbbe_schema_migrations
		(version, name, checksum) VALUES (1, 'fixture', 'checksum')`); err != nil {
		t.Fatal("writer did not recover after guard release:", err)
	}
}

func TestDisposablePostgresAdvisoryContentionRefused(t *testing.T) {
	dsn := isolatedDSN(t)
	owner := fixtureConnection(t, dsn)
	prepareEmptyLedger(t, owner)
	var granted bool
	if err := owner.QueryRow(context.Background(), "SELECT pg_try_advisory_lock($1)", migrationAdvisoryLockID).Scan(&granted); err != nil || !granted {
		t.Fatal("could not establish competing advisory lock", err)
	}
	defer owner.Exec(context.Background(), "SELECT pg_advisory_unlock($1)", migrationAdvisoryLockID)
	guard := startGuard(t, compileGuard(t), dsn)
	guardStops(t, guard)
}

func TestDisposablePostgresConcurrentWriterRefused(t *testing.T) {
	dsn := isolatedDSN(t)
	owner := fixtureConnection(t, dsn)
	prepareEmptyLedger(t, owner)
	ctx := context.Background()
	tx, err := owner.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "LOCK TABLE public.evcowbbe_schema_migrations IN ROW EXCLUSIVE MODE"); err != nil {
		t.Fatal(err)
	}
	guard := startGuard(t, compileGuard(t), dsn)
	guardStops(t, guard)
}

func TestDisposablePostgresNonemptyLedgerRefused(t *testing.T) {
	dsn := isolatedDSN(t)
	owner := fixtureConnection(t, dsn)
	prepareEmptyLedger(t, owner)
	if _, err := owner.Exec(context.Background(), `INSERT INTO public.evcowbbe_schema_migrations
		(version, name, checksum) VALUES (1, 'fixture', 'checksum')`); err != nil {
		t.Fatal(err)
	}
	guard := startGuard(t, compileGuard(t), dsn)
	guardStops(t, guard)
}

func TestDisposablePostgresControllerEOFReleasesFence(t *testing.T) {
	dsn := isolatedDSN(t)
	owner := fixtureConnection(t, dsn)
	prepareEmptyLedger(t, owner)
	guard := startGuard(t, compileGuard(t), dsn)
	guardLine(t, guard)
	if err := guard.stdin.Close(); err != nil {
		t.Fatal(err)
	}
	guardStops(t, guard)
	writer := fixtureConnection(t, dsn)
	if _, err := writer.Exec(context.Background(), `INSERT INTO public.evcowbbe_schema_migrations
		(version, name, checksum) VALUES (1, 'fixture', 'checksum')`); err != nil {
		t.Fatal("guard retained writer lock after controller EOF:", err)
	}
}

func TestDisposablePostgresLostSessionFailsClosed(t *testing.T) {
	dsn := isolatedDSN(t)
	owner := fixtureConnection(t, dsn)
	prepareEmptyLedger(t, owner)
	uniqueDSN := dsn + "&application_name=evcowbbe-recovery-fixture-guard"
	guard := startGuard(t, compileGuard(t), uniqueDSN)
	guardLine(t, guard)
	var pid int
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := owner.QueryRow(ctx, `SELECT pid FROM pg_stat_activity WHERE application_name =
		'evcowbbe-recovery-fixture-guard' AND pid <> pg_backend_pid()`).Scan(&pid)
	if err != nil || pid <= 1 {
		t.Fatal("cannot locate disposable test guard backend", err)
	}
	var killed bool
	if err := owner.QueryRow(ctx, "SELECT pg_terminate_backend($1)", pid).Scan(&killed); err != nil || !killed {
		t.Fatal("failed to terminate isolated test backend", err)
	}
	_, _ = io.WriteString(guard.stdin, "ping\n")
	guardStops(t, guard)
}

func TestDisposablePostgresUnsafeEnvironmentRefused(t *testing.T) {
	good := "postgres://recovery_ci:recovery_ci@127.0.0.1:5432/evcowbbe_recovery_ci?sslmode=disable"
	bad := []struct {
		url        string
		authorized bool
	}{
		{good, false},
		{"postgres://recovery_ci:recovery_ci@198.51.100.10:5432/evcowbbe_recovery_ci?sslmode=disable", true},
		{"postgres://recovery_ci:recovery_ci@127.0.0.1:5432/production?sslmode=disable", true},
		{"postgres://postgres:recovery_ci@127.0.0.1:5432/evcowbbe_recovery_ci?sslmode=disable", true},
		{"postgres://recovery_ci:recovery_ci@127.0.0.1:5432/evcowbbe_recovery_ci?sslmode=require", true},
		{"postgres://recovery_ci:recovery_ci@127.0.0.1:5432/evcowbbe_recovery_ci?sslmode=disable&application_name=x", true},
	}
	for _, item := range bad {
		if err := validateIsolatedDSN(item.url, item.authorized); err == nil {
			t.Fatal("unsafe fixture unexpectedly accepted")
		}
	}
	if err := validateIsolatedDSN(good, true); err != nil {
		t.Fatal("exact disposable fixture was rejected", err)
	}
}
