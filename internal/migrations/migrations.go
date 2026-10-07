package migrations

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	migrationfiles "github.com/Transmogriffy-Global-Private-Limited/evcowbbe/db/migrations"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	ledgerTableName       = "evcowbbe_schema_migrations"
	advisoryLockID  int64 = 44727018461933121
)

var (
	ErrLedgerMissing    = errors.New("migration ledger is missing")
	ErrChecksumMismatch = errors.New("migration checksum mismatch")
	ErrMigrationPending = errors.New("migration is pending")
	ErrLockBusy         = errors.New("migration lock is busy")
	fileNameRegexp      = regexp.MustCompile(`^([0-9]+)_([a-z0-9][a-z0-9_]*)\.sql$`)
)

type Migration struct {
	Version  int64
	Name     string
	SQL      string
	Checksum string
}

type AppliedMigration struct {
	Version      int64
	Name         string
	Checksum     string
	AppliedAt    time.Time
	SourceGitSHA *string
}

type Status struct {
	LedgerPresent bool
	Applied       []AppliedMigration
	Pending       []Migration
}

// Manager owns migration ledger integrity and application for one database.
type Manager struct {
	pool        *pgxpool.Pool
	migrations  []Migration
	ledgerTable string
	lockID      int64
}

func New(pool *pgxpool.Pool) *Manager {
	return &Manager{
		pool:        pool,
		migrations:  Files(),
		ledgerTable: ledgerTableName,
		lockID:      advisoryLockID,
	}
}

// Files returns the statically validated migration set embedded in the binary.
// A deployment therefore verifies the exact migration files compiled from its
// source checkout, rather than relying on a mutable runtime path.
func Files() []Migration {
	migrations, err := LoadFiles(migrationfiles.FS)
	if err != nil {
		panic(fmt.Sprintf("invalid embedded migrations: %v", err))
	}

	return migrations
}

// LoadFiles validates and loads migration SQL from a filesystem. It is exposed
// for static verification and tests; production uses the embedded filesystem.
func LoadFiles(filesystem fs.FS) ([]Migration, error) {
	entries, err := fs.ReadDir(filesystem, ".")
	if err != nil {
		return nil, fmt.Errorf("read migrations: %w", err)
	}

	migrations := make([]Migration, 0)
	seenVersions := make(map[int64]struct{})

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".sql") {
			continue
		}

		match := fileNameRegexp.FindStringSubmatch(entry.Name())
		if match == nil {
			return nil, fmt.Errorf("invalid migration filename %q", entry.Name())
		}

		version, err := strconv.ParseInt(match[1], 10, 64)
		if err != nil || version <= 0 {
			return nil, fmt.Errorf("invalid migration version in %q", entry.Name())
		}
		if _, exists := seenVersions[version]; exists {
			return nil, fmt.Errorf("duplicate migration version %d", version)
		}

		contents, err := fs.ReadFile(filesystem, entry.Name())
		if err != nil {
			return nil, fmt.Errorf("read migration %q: %w", entry.Name(), err)
		}
		if len(contents) == 0 {
			return nil, fmt.Errorf("migration %q is empty", entry.Name())
		}

		checksum := sha256.Sum256(contents)
		migrations = append(migrations, Migration{
			Version:  version,
			Name:     match[2],
			SQL:      string(contents),
			Checksum: hex.EncodeToString(checksum[:]),
		})
		seenVersions[version] = struct{}{}
	}

	sort.Slice(migrations, func(i, j int) bool {
		return migrations[i].Version < migrations[j].Version
	})

	return migrations, nil
}

// VerifyFiles verifies the embedded migration metadata without a database.
func VerifyFiles() error {
	_, err := LoadFiles(migrationfiles.FS)
	return err
}

// Apply creates the migration ledger if necessary and applies each pending
// migration in its own database transaction. releaseSHA is NULL for an honest
// development build without a real embedded release identity.
func (m *Manager) Apply(ctx context.Context, releaseSHA string) error {
	if err := validateOptionalReleaseSHA(releaseSHA); err != nil {
		return err
	}

	return m.withLock(ctx, false, func(conn *pgxpool.Conn) error {
		if err := m.ensureLedger(ctx, conn); err != nil {
			return err
		}

		applied, err := m.applied(ctx, conn)
		if err != nil {
			return err
		}
		if err := m.verifyApplied(applied); err != nil {
			return err
		}

		for _, migration := range m.migrations {
			if _, exists := applied[migration.Version]; exists {
				continue
			}

			if err := m.applyOne(ctx, conn, migration, releaseSHA); err != nil {
				return err
			}
		}

		return nil
	})
}

// Verify checks the durable ledger against the migrations embedded in this
// binary. It does not apply migrations or create missing metadata.
func (m *Manager) Verify(ctx context.Context) error {
	return m.withLock(ctx, false, func(conn *pgxpool.Conn) error {
		present, err := m.ledgerExists(ctx, conn)
		if err != nil {
			return err
		}
		if !present {
			return ErrLedgerMissing
		}

		applied, err := m.applied(ctx, conn)
		if err != nil {
			return err
		}
		return m.verifyApplied(applied)
	})
}

// Status reports durable migration state without applying or changing it.
func (m *Manager) Status(ctx context.Context) (Status, error) {
	status := Status{}
	err := m.withLock(ctx, false, func(conn *pgxpool.Conn) error {
		present, err := m.ledgerExists(ctx, conn)
		if err != nil {
			return err
		}
		if !present {
			return nil
		}
		status.LedgerPresent = true

		applied, err := m.applied(ctx, conn)
		if err != nil {
			return err
		}
		if err := m.verifyApplied(applied); err != nil {
			return err
		}

		status.Applied = orderedApplied(applied)
		for _, migration := range m.migrations {
			if _, exists := applied[migration.Version]; !exists {
				status.Pending = append(status.Pending, migration)
			}
		}
		return nil
	})

	return status, err
}

// CheckReady is the read-only schema-compatibility predicate used by HTTP
// readiness. A migration in progress intentionally makes readiness fail rather
// than racing a schema transition.
func (m *Manager) CheckReady(ctx context.Context) error {
	return m.withLock(ctx, true, func(conn *pgxpool.Conn) error {
		present, err := m.ledgerExists(ctx, conn)
		if err != nil {
			return err
		}
		if !present {
			return ErrLedgerMissing
		}

		applied, err := m.applied(ctx, conn)
		if err != nil {
			return err
		}
		if err := m.verifyApplied(applied); err != nil {
			return err
		}
		for _, migration := range m.migrations {
			if _, exists := applied[migration.Version]; !exists {
				return fmt.Errorf("%w: %d_%s", ErrMigrationPending, migration.Version, migration.Name)
			}
		}

		return nil
	})
}

func (m *Manager) applyOne(
	ctx context.Context,
	conn *pgxpool.Conn,
	migration Migration,
	releaseSHA string,
) error {
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin migration %d: %w", migration.Version, err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, migration.SQL); err != nil {
		return fmt.Errorf("apply migration %d_%s: %w", migration.Version, migration.Name, err)
	}

	if _, err := tx.Exec(
		ctx,
		fmt.Sprintf(`INSERT INTO %s (version, name, checksum, source_git_sha) VALUES ($1, $2, $3, $4)`, m.quotedLedgerTable()),
		migration.Version,
		migration.Name,
		migration.Checksum,
		nullableReleaseSHA(releaseSHA),
	); err != nil {
		return fmt.Errorf("record migration %d_%s: %w", migration.Version, migration.Name, err)
	}

	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit migration %d_%s: %w", migration.Version, migration.Name, err)
	}

	return nil
}

func (m *Manager) ensureLedger(ctx context.Context, conn *pgxpool.Conn) error {
	query := fmt.Sprintf(`CREATE TABLE IF NOT EXISTS %s (
		version BIGINT PRIMARY KEY,
		name TEXT NOT NULL CHECK (name <> ''),
		checksum CHAR(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
		applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
		source_git_sha CHAR(40) NULL CHECK (source_git_sha IS NULL OR source_git_sha ~ '^[0-9a-f]{40}$')
	)`, m.quotedLedgerTable())
	if _, err := conn.Exec(ctx, query); err != nil {
		return fmt.Errorf("create migration ledger: %w", err)
	}
	return nil
}

func (m *Manager) ledgerExists(ctx context.Context, conn *pgxpool.Conn) (bool, error) {
	var present bool
	if err := conn.QueryRow(
		ctx,
		`SELECT to_regclass('public.' || $1) IS NOT NULL`,
		m.ledgerTable,
	).Scan(&present); err != nil {
		return false, fmt.Errorf("check migration ledger: %w", err)
	}
	return present, nil
}

func (m *Manager) applied(ctx context.Context, conn *pgxpool.Conn) (map[int64]AppliedMigration, error) {
	rows, err := conn.Query(
		ctx,
		fmt.Sprintf(`SELECT version, name, checksum, applied_at, source_git_sha FROM %s ORDER BY version`, m.quotedLedgerTable()),
	)
	if err != nil {
		return nil, fmt.Errorf("read migration ledger: %w", err)
	}
	defer rows.Close()

	applied := make(map[int64]AppliedMigration)
	for rows.Next() {
		var record AppliedMigration
		if err := rows.Scan(
			&record.Version,
			&record.Name,
			&record.Checksum,
			&record.AppliedAt,
			&record.SourceGitSHA,
		); err != nil {
			return nil, fmt.Errorf("scan migration ledger: %w", err)
		}
		applied[record.Version] = record
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate migration ledger: %w", err)
	}
	return applied, nil
}

func (m *Manager) verifyApplied(applied map[int64]AppliedMigration) error {
	known := make(map[int64]Migration, len(m.migrations))
	for _, migration := range m.migrations {
		known[migration.Version] = migration
	}

	for version, record := range applied {
		migration, exists := known[version]
		if !exists {
			return fmt.Errorf("%w: applied version %d is not present in this binary", ErrChecksumMismatch, version)
		}
		if record.Name != migration.Name || record.Checksum != migration.Checksum {
			return fmt.Errorf("%w: version %d", ErrChecksumMismatch, version)
		}
	}

	return nil
}

func (m *Manager) withLock(
	ctx context.Context,
	try bool,
	operation func(*pgxpool.Conn) error,
) error {
	if m.pool == nil {
		return errors.New("migration database pool is nil")
	}

	conn, err := m.pool.Acquire(ctx)
	if err != nil {
		return fmt.Errorf("acquire migration connection: %w", err)
	}
	defer conn.Release()

	if try {
		var locked bool
		if err := conn.QueryRow(ctx, `SELECT pg_try_advisory_lock($1)`, m.lockID).Scan(&locked); err != nil {
			return fmt.Errorf("try migration advisory lock: %w", err)
		}
		if !locked {
			return ErrLockBusy
		}
	} else if _, err := conn.Exec(ctx, `SELECT pg_advisory_lock($1)`, m.lockID); err != nil {
		return fmt.Errorf("acquire migration advisory lock: %w", err)
	}
	defer func() {
		_, _ = conn.Exec(context.Background(), `SELECT pg_advisory_unlock($1)`, m.lockID)
	}()

	return operation(conn)
}

func (m *Manager) quotedLedgerTable() string {
	return `"public".` + quoteIdentifier(m.ledgerTable)
}

func quoteIdentifier(value string) string {
	return `"` + strings.ReplaceAll(value, `"`, `""`) + `"`
}

func orderedApplied(applied map[int64]AppliedMigration) []AppliedMigration {
	result := make([]AppliedMigration, 0, len(applied))
	for _, migration := range applied {
		result = append(result, migration)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Version < result[j].Version })
	return result
}

func validateOptionalReleaseSHA(value string) error {
	if value != "" && !buildinfo.IsValidGitSHA(value) {
		return fmt.Errorf("migration source Git SHA must be a 40-character lowercase Git SHA when set")
	}
	return nil
}

func nullableReleaseSHA(value string) any {
	if value == "" {
		return nil
	}
	return value
}
