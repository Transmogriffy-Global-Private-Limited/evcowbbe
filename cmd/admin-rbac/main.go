// Initial Admin promotion is an operator-only, one-time, offline action.
// No HTTP endpoint automatically promotes an Admin token to FULL_ADMIN.
package main

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
)

func main() {
	if len(os.Args) != 3 || os.Args[1] != "grant-initial-full-admin" {
		fmt.Fprintln(os.Stderr, "usage: go run ./cmd/admin-rbac grant-initial-full-admin EMAIL")
		os.Exit(2)
	}
	email, err := security.Email(os.Args[2])
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid Admin email")
		os.Exit(2)
	}
	fmt.Fprintf(os.Stderr, "This grants the first FULL_ADMIN role to existing Admin %s. Type GRANT INITIAL FULL_ADMIN to proceed: ", email)
	line, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if err != nil || strings.TrimSpace(line) != "GRANT INITIAL FULL_ADMIN" {
		fmt.Fprintln(os.Stderr, "cancelled")
		os.Exit(2)
	}
	c, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid configuration")
		os.Exit(1)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	pool, err := database.Open(ctx, c.DatabaseURL, c.DBMaxConns)
	if err != nil {
		fmt.Fprintln(os.Stderr, "database unavailable")
		os.Exit(1)
	}
	defer pool.Close()
	if err = database.VerifySchema(ctx, pool); err != nil {
		fmt.Fprintln(os.Stderr, "migrations pending or drifted")
		os.Exit(1)
	}
	tx, err := pool.Begin(ctx)
	if err != nil {
		fmt.Fprintln(os.Stderr, "transaction unavailable")
		os.Exit(1)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(1437519,20261027)`); err != nil {
		fmt.Fprintln(os.Stderr, "lock unavailable")
		os.Exit(1)
	}
	var existing int
	if err = tx.QueryRow(ctx, `SELECT COUNT(*) FROM admin_role_assignments`).Scan(&existing); err != nil || existing != 0 {
		fmt.Fprintln(os.Stderr, "role assignments already exist; initial bootstrap is closed")
		os.Exit(1)
	}
	var account string
	err = tx.QueryRow(ctx, `SELECT a.id FROM accounts a JOIN principals p ON p.account_id=a.id
 WHERE a.email=$1 AND a.email_verified_at IS NOT NULL AND a.disabled_at IS NULL
 AND p.plane='admin' AND p.status='ACTIVE' FOR UPDATE OF a`, email).Scan(&account)
	if err != nil {
		fmt.Fprintln(os.Stderr, "active, verified Admin not found")
		os.Exit(1)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO admin_role_assignments(account_id,role_key) VALUES($1,'FULL_ADMIN')`, account); err != nil {
		fmt.Fprintln(os.Stderr, "role grant failed")
		os.Exit(1)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO admin_rbac_audit(target_id,operation,subject,reason) VALUES($1,'INITIAL_OFFLINE_GRANT','FULL_ADMIN','Explicit manual operator bootstrap')`, account); err != nil {
		fmt.Fprintln(os.Stderr, "audit failed")
		os.Exit(1)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO security_audit(account_id,plane,event) VALUES($1,'admin','INITIAL_FULL_ADMIN_GRANTED')`, account); err != nil {
		fmt.Fprintln(os.Stderr, "audit failed")
		os.Exit(1)
	}
	if err = tx.Commit(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "transaction failed")
		os.Exit(1)
	}
	fmt.Fprintln(os.Stdout, "Initial FULL_ADMIN grant committed; no existing sessions changed")
}
