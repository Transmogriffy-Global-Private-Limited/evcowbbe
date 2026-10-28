package database

import (
	"errors"

	"entgo.io/ent/dialect"
	entsql "entgo.io/ent/dialect/sql"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/jackc/pgx/v5/stdlib"

	appent "github.com/Transmogriffy-Global-Private-Limited/evcowbbe/ent"
)

// OpenEntClient exposes the typed customer model without opening a second
// PostgreSQL connection pool. The caller owns the Ent client and must Close it.
// Closing this client does NOT close the shared pgxpool.Pool.
//
// IMPORTANT: This function never creates/modifies schema. Applied, checked SQL
// migration versions (001-005) remain the authority. Do not call Schema.Create
// at startup. Do not assume an Ent transaction is the same as a pgx.Tx: compose
// business writes/audit/outbox within ONE transaction on ONE connection.
func OpenEntClient(pool *pgxpool.Pool) (*appent.Client, error) {
	if pool == nil {
		return nil, errors.New("nil PostgreSQL pool")
	}
	return appent.NewClient(appent.Driver(entsql.OpenDB(dialect.Postgres, stdlib.OpenDBFromPool(pool)))), nil
}
