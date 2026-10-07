package database

import (
	"context"
	"errors"
	"testing"
)

func TestOpenRejectsEmptyDatabaseURL(t *testing.T) {
	pool, err := Open(context.Background(), "")

	if pool != nil {
		pool.Close()
		t.Fatal("expected no pool")
	}

	if !errors.Is(err, ErrDatabaseURLRequired) {
		t.Fatalf(
			"expected ErrDatabaseURLRequired, got %v",
			err,
		)
	}
}

func TestOpenRejectsInvalidDatabaseURL(t *testing.T) {
	pool, err := Open(
		context.Background(),
		"postgres://%",
	)

	if pool != nil {
		pool.Close()
		t.Fatal("expected no pool")
	}

	if !errors.Is(err, ErrDatabaseURLInvalid) {
		t.Fatalf(
			"expected ErrDatabaseURLInvalid, got %v",
			err,
		)
	}
}
