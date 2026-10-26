package database

import (
	"context"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed migrations/*.sql
var embedded embed.FS

func Open(ctx context.Context, uri string, maxConns int32) (*pgxpool.Pool, error) {
	config, err := pgxpool.ParseConfig(uri)
	if err != nil {
		return nil, errors.New("invalid database connection settings")
	}
	config.MaxConns = maxConns
	config.MaxConnLifetime = time.Hour
	p, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, errors.New("database pool initialization failed")
	}
	ctx, t := context.WithTimeout(ctx, 8*time.Second)
	defer t()
	if err = p.Ping(ctx); err != nil {
		p.Close()
		return nil, errors.New("database unavailable")
	}
	return p, nil
}
func Migrate(ctx context.Context, p *pgxpool.Pool, mode string) error {
	if mode != "up" && mode != "status" {
		return errors.New("expected up or status")
	}
	conn, err := p.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()
	_, err = conn.Exec(ctx, `SELECT pg_advisory_lock(1437519, 20261026)`)
	if err != nil {
		return err
	}
	defer conn.Exec(context.Background(), `SELECT pg_advisory_unlock(1437519, 20261026)`)
	// Status never creates or updates schema objects. Only an explicit `up`
	// command is allowed to initialize migration bookkeeping.
	if mode == "up" {
		_, err = conn.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())`)
		if err != nil {
			return err
		}
	}
	entries, err := embedded.ReadDir("migrations")
	if err != nil {
		return err
	}
	files := make([]string, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".sql") {
			files = append(files, entry.Name())
		}
	}
	sort.Strings(files)
	for _, file := range files {
		version, err := strconv.Atoi(strings.SplitN(file, "_", 2)[0])
		if err != nil || version < 1 {
			return errors.New("invalid migration name")
		}
		raw, err := embedded.ReadFile("migrations/" + file)
		if err != nil {
			return err
		}
		sum := sha256.Sum256(raw)
		checksum := hex.EncodeToString(sum[:])
		var prior string
		err = conn.QueryRow(ctx, `SELECT checksum FROM schema_migrations WHERE version=$1`, version).Scan(&prior)
		if err == nil {
			if prior != checksum {
				return fmt.Errorf("migration %d checksum drift", version)
			}
			fmt.Printf("migration %d already applied\n", version)
			continue
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		if mode == "status" {
			fmt.Printf("migration %d pending\n", version)
			continue
		}
		tx, err := conn.Begin(ctx)
		if err != nil {
			return err
		}
		// Each embedded migration contains multiple SQL statements. PostgreSQL's
		// extended query protocol cannot prepare them as one statement; explicitly
		// use pgx's simple protocol inside the same migration transaction.
		if _, err = tx.Exec(ctx, string(raw), pgx.QueryExecModeSimpleProtocol); err != nil {
			tx.Rollback(ctx)
			return fmt.Errorf("migration %d SQL failed: %w", version, err)
		}
		if _, err = tx.Exec(ctx, `INSERT INTO schema_migrations (version,checksum) VALUES ($1,$2)`, version, checksum); err != nil {
			tx.Rollback(ctx)
			return err
		}
		if err = tx.Commit(ctx); err != nil {
			return err
		}
		fmt.Printf("migration %d applied\n", version)
	}
	return nil
}

// VerifySchema is read-only and prevents accidentally running against an
// uninitialized or drifted database. Every migration currently embedded must match.
func VerifySchema(ctx context.Context, p *pgxpool.Pool) error {
	entries, err := embedded.ReadDir("migrations")
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".sql") {
			continue
		}
		version, err := strconv.Atoi(strings.SplitN(entry.Name(), "_", 2)[0])
		if err != nil {
			return errors.New("invalid embedded migration")
		}
		raw, err := embedded.ReadFile("migrations/" + entry.Name())
		if err != nil {
			return err
		}
		checksum := sha256.Sum256(raw)
		var actual string
		if err = p.QueryRow(ctx, `SELECT checksum FROM schema_migrations WHERE version=$1`, version).Scan(&actual); err != nil {
			return errors.New("database migration missing")
		}
		if actual != hex.EncodeToString(checksum[:]) {
			return errors.New("database migration checksum mismatch")
		}
	}
	return nil
}
