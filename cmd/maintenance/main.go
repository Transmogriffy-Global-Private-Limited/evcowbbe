package main

import (
	"context"
	"fmt"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"os"
	"time"
)

func main() {
	if len(os.Args) != 2 || os.Args[1] != "cleanup" {
		fmt.Fprintln(os.Stderr, "usage: go run ./cmd/maintenance cleanup")
		os.Exit(2)
	}
	c, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid configuration")
		os.Exit(1)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	db, err := database.Open(ctx, c.DatabaseURL, c.DBMaxConns)
	if err != nil {
		fmt.Fprintln(os.Stderr, "database unavailable")
		os.Exit(1)
	}
	defer db.Close()
	// Preserve security_audit and current token history. Delete only expired, stale records.
	queries := []string{
		`UPDATE auth_mail_outbox SET state='EXPIRED',payload_ciphertext=NULL,payload_nonce=NULL,lease_token=NULL,lease_until=NULL,updated_at=now(),last_error_class='source_expired' WHERE expires_at<now() AND (state='PENDING' OR (state='LEASED' AND lease_until<now()))`,
		`DELETE FROM auth_mail_outbox WHERE state IN ('SENT','EXPIRED','DEAD') AND updated_at<now()-interval '30 days'`,
		`DELETE FROM auth_challenges WHERE expires_at<now()-interval '2 days'`,
		`DELETE FROM auth_action_tokens WHERE expires_at<now()-interval '2 days'`,
		`DELETE FROM auth_sessions WHERE absolute_expires_at<now()-interval '7 days'`,
		`DELETE FROM auth_rate_limits WHERE window_start<now()-interval '2 days'`,
	}
	for _, q := range queries {
		if _, err = db.Exec(ctx, q); err != nil {
			fmt.Fprintln(os.Stderr, "cleanup failed")
			os.Exit(1)
		}
	}
	fmt.Println("Expired ephemeral authentication records cleaned; accounts and audits preserved.")
}
