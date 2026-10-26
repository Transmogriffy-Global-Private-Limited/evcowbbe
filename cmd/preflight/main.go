// Read-only live PostgreSQL and SMTP connectivity preflight.
// Does not create tables, run migrations, send email or disclose credentials.
package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/mailer"
)

func main() {
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "usage: go run ./cmd/preflight")
		os.Exit(2)
	}
	c, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "CONFIGURATION=FAILED:", err)
		os.Exit(1)
	}
	fmt.Println("CONFIGURATION=PASS")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	db, err := database.Open(ctx, c.DatabaseURL, 1)
	if err != nil {
		fmt.Fprintln(os.Stderr, "POSTGRES_CONNECTION=FAILED")
		os.Exit(1)
	}
	db.Close()
	fmt.Println("POSTGRES_CONNECTION=PASS")
	if err := (mailer.Sender{Cfg: c}).Probe(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "SMTP_HANDSHAKE=FAILED:", err)
		os.Exit(1)
	}
	fmt.Println("SMTP_HANDSHAKE=PASS")
	fmt.Println("NOTE=SMTP delivery has NOT been tested")
}
