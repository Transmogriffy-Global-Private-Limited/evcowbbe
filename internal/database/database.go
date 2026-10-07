package database

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

const startupPingTimeout = 5 * time.Second

var (
	ErrDatabaseURLRequired = errors.New("DATABASE_URL is required")
	ErrDatabaseURLInvalid  = errors.New("DATABASE_URL is invalid")
	ErrDatabaseUnavailable = errors.New("database unavailable")
)

func Open(
	ctx context.Context,
	databaseURL string,
) (*pgxpool.Pool, error) {
	if strings.TrimSpace(databaseURL) == "" {
		return nil, ErrDatabaseURLRequired
	}

	cfg, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		return nil, ErrDatabaseURLInvalid
	}

	if cfg.ConnConfig.RuntimeParams == nil {
		cfg.ConnConfig.RuntimeParams = make(map[string]string)
	}

	cfg.ConnConfig.RuntimeParams["application_name"] = "evcowbbe"

	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, ErrDatabaseUnavailable
	}

	pingCtx, cancel := context.WithTimeout(
		ctx,
		startupPingTimeout,
	)
	defer cancel()

	if err := pool.Ping(pingCtx); err != nil {
		pool.Close()
		return nil, ErrDatabaseUnavailable
	}

	return pool, nil
}
