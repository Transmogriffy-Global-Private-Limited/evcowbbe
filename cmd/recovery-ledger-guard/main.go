// recovery-ledger-guard holds a PostgreSQL migration exclusion fence for one
// explicitly authorized, incident-specific SQLite reconciliation. No SQL writes.
// The process owns the advisory and table locks until it receives RELEASE or
// the controller's stdin closes. It MUST be executed as the evcowbbe identity.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
)

const migrationAdvisoryLockID int64 = 44727018461933121
const expectedEmptySHA256 = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"

type reply struct {
	State         string `json:"state"`
	LedgerPresent bool   `json:"ledger_present"`
	Applied       int64  `json:"applied"`
	Fingerprint   string `json:"fingerprint"`
}

func send(state string) error {
	return json.NewEncoder(os.Stdout).Encode(reply{
		State: state, LedgerPresent: true, Applied: 0, Fingerprint: expectedEmptySHA256,
	})
}

func run() error {
	if len(os.Args) != 1 {
		return errors.New("no arguments permitted")
	}
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		return errors.New("database credential unavailable")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		return errors.New("database connection unavailable")
	}
	defer conn.Close(context.Background())
	var acquired bool
	if err := conn.QueryRow(ctx, `SELECT pg_try_advisory_lock($1)`, migrationAdvisoryLockID).Scan(&acquired); err != nil || !acquired {
		return errors.New("migration advisory fence unavailable")
	}
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.ReadCommitted, AccessMode: pgx.ReadWrite})
	if err != nil {
		return errors.New("cannot acquire read/write locking transaction")
	}
	defer tx.Rollback(context.Background())
	// SHARE conflicts with ROW EXCLUSIVE used by INSERT, UPDATE and DELETE,
	// and conflicts with ACCESS EXCLUSIVE DDL; NOWAIT refuses contention.
	if _, err := tx.Exec(ctx, `LOCK TABLE public.evcowbbe_schema_migrations IN SHARE MODE NOWAIT`); err != nil {
		return errors.New("migration ledger writer fence unavailable")
	}
	var count int64
	if err := tx.QueryRow(ctx, `SELECT count(*) FROM public.evcowbbe_schema_migrations`).Scan(&count); err != nil || count != 0 {
		return errors.New("ledger is missing, inaccessible or changed")
	}
	if err := send("held"); err != nil {
		return errors.New("fence acknowledgement unavailable")
	}
	reader := bufio.NewScanner(os.Stdin)
	reader.Buffer(make([]byte, 128), 512)
	for reader.Scan() {
		switch reader.Text() {
		case "ping":
			// Query through the same transaction to ensure the session and
			// SHARE lock have not been abandoned.
			pingCtx, stop := context.WithTimeout(context.Background(), 5*time.Second)
			err := tx.QueryRow(pingCtx, `SELECT count(*) FROM public.evcowbbe_schema_migrations`).Scan(&count)
			stop()
			if err != nil || count != 0 {
				return errors.New("fenced ledger changed")
			}
			if err := send("held"); err != nil {
				return errors.New("fence acknowledgement lost")
			}
		case "release":
			return nil
		default:
			return errors.New("invalid control instruction")
		}
	}
	if err := reader.Err(); err != nil {
		return errors.New("fence control channel failed")
	}
	// EOF before release means the coordinator died; never emit success.
	return errors.New("fence controller disconnected")
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "recovery ledger fence unavailable")
		os.Exit(5)
	}
}
