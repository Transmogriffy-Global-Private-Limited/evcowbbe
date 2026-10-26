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
	if len(os.Args) != 2 || (os.Args[1] != "up" && os.Args[1] != "status") {
		fmt.Fprintln(os.Stderr, "usage: go run ./cmd/migrate up|status")
		os.Exit(2)
	}
	c, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "configuration invalid:", err)
		os.Exit(1)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	db, err := database.Open(ctx, c.DatabaseURL, c.DBMaxConns)
	if err != nil {
		fmt.Fprintln(os.Stderr, "database unavailable")
		os.Exit(1)
	}
	defer db.Close()
	if err = database.Migrate(ctx, db, os.Args[1]); err != nil {
		fmt.Fprintln(os.Stderr, "migration failed:", err)
		os.Exit(1)
	}
}
