package database

import (
	"context"
	"os"
	"testing"
	"time"
)

func TestOpenWithDisposablePostgreSQL(t *testing.T) {
	databaseURL := os.Getenv("TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip(
			"TEST_DATABASE_URL not set; disposable PostgreSQL integration test skipped",
		)
	}

	ctx, cancel := context.WithTimeout(
		context.Background(),
		10*time.Second,
	)
	defer cancel()

	pool, err := Open(ctx, databaseURL)
	if err != nil {
		t.Fatalf("Open returned error: %v", err)
	}
	defer pool.Close()

	if err := pool.Ping(ctx); err != nil {
		t.Fatalf("Ping returned error: %v", err)
	}
}
