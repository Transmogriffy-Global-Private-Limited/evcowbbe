// ledger-observer is a read-only snapshot of the actual PostgreSQL migration
// ledger, independent of the deployed application's migrator version.
// Its binary is compiled from the reviewed control-plane release, not from
// whichever server release is currently active.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const lockID int64 = 44727018461933121

type ledgerIdentity struct {
	Version                    int64  `json:"version"`
	Name                       string `json:"name"`
	Checksum                   string `json:"checksum"`
	MinCompatibleBinaryVersion *int64 `json:"min_compatible_binary_version"`
}

type ledgerSnapshot struct {
	LedgerPresent bool             `json:"ledger_present"`
	Applied       []ledgerIdentity `json:"applied"`
}

func observe(ctx context.Context, pool *pgxpool.Pool) (ledgerSnapshot, error) {
	result := ledgerSnapshot{Applied: make([]ledgerIdentity, 0)}
	conn, err := pool.Acquire(ctx)
	if err != nil {
		return result, err
	}
	defer conn.Release()

	// Coordinate with the application's migration session advisory lock.
	// A concurrent migration must not yield a misleading partial snapshot.
	var locked bool
	if err = conn.QueryRow(ctx, `SELECT pg_try_advisory_lock($1)`, lockID).Scan(&locked); err != nil {
		return result, err
	}
	if !locked {
		return result, errors.New("migration in progress")
	}
	defer func() {
		unlockCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_, _ = conn.Exec(unlockCtx, `SELECT pg_advisory_unlock($1)`, lockID)
	}()

	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return result, err
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	if err = tx.QueryRow(ctx, `SELECT to_regclass('public.evcowbbe_schema_migrations') IS NOT NULL`).Scan(&result.LedgerPresent); err != nil {
		return result, err
	}
	if result.LedgerPresent {
		rows, err := tx.Query(ctx, `SELECT version, name, checksum, min_compatible_binary_version FROM public.evcowbbe_schema_migrations ORDER BY version`)
		if err != nil {
			return result, err
		}
		defer rows.Close()
		for rows.Next() {
			var record ledgerIdentity
			if err := rows.Scan(&record.Version, &record.Name, &record.Checksum, &record.MinCompatibleBinaryVersion); err != nil {
				return result, err
			}
			result.Applied = append(result.Applied, record)
			if len(result.Applied) > 100000 {
				return result, errors.New("ledger exceeds observer bound")
			}
		}
		if err = rows.Err(); err != nil {
			return result, err
		}
	}
	if err = tx.Commit(ctx); err != nil {
		return result, err
	}
	return result, nil
}

func main() {
	// No caller arguments, no SQL writes and no application release identity
	// requirement. DATABASE_URL is provided by the existing systemd
	// EnvironmentFile under the evcowbbe identity.
	if len(os.Args) != 1 {
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	pool, err := database.Open(ctx, os.Getenv("DATABASE_URL"))
	if err == nil {
		defer pool.Close()
		var snapshot ledgerSnapshot
		snapshot, err = observe(ctx, pool)
		if err == nil {
			err = json.NewEncoder(os.Stdout).Encode(snapshot)
		}
	}
	if err != nil {
		// Do not leak DB host, user, password, query text, or driver errors.
		fmt.Fprintln(os.Stderr, "ledger observation unavailable")
		os.Exit(1)
	}
}
