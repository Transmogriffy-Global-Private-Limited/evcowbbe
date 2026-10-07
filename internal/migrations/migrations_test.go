package migrations

import (
	"errors"
	"fmt"
	"testing"
	"testing/fstest"
)

func TestLoadFilesOrdersChecksumsAndCompatibilityFloors(t *testing.T) {
	files := fstest.MapFS{
		"000010_second.sql": {Data: migrationSQL(5, "SELECT 2;\n")},
		"000002_first.sql":  {Data: migrationSQL(0, "SELECT 1;\n")},
	}

	migrations, err := LoadFiles(files)
	if err != nil {
		t.Fatalf("LoadFiles returned error: %v", err)
	}

	if len(migrations) != 2 {
		t.Fatalf("expected two migrations, got %d", len(migrations))
	}
	if migrations[0].Version != 2 || migrations[0].Name != "first" || migrations[0].MinCompatibleBinaryVersion != 0 {
		t.Fatalf("unexpected first migration: %#v", migrations[0])
	}
	if migrations[1].MinCompatibleBinaryVersion != 5 {
		t.Fatalf("expected compatibility floor 5, got %d", migrations[1].MinCompatibleBinaryVersion)
	}
	if migrations[0].Checksum == migrations[1].Checksum {
		t.Fatal("different SQL files must have different checksums")
	}
}

func TestLoadFilesRejectsInvalidCompatibilityMetadata(t *testing.T) {
	tests := []struct {
		name string
		data []byte
	}{
		{name: "missing", data: []byte("SELECT 1;\n")},
		{name: "malformed", data: []byte("-- evcowbbe:min-compatible-binary-version=nope\nSELECT 1;\n")},
		{name: "negative", data: []byte("-- evcowbbe:min-compatible-binary-version=-1\nSELECT 1;\n")},
		{name: "duplicate", data: []byte("-- evcowbbe:min-compatible-binary-version=0\n-- evcowbbe:min-compatible-binary-version=0\nSELECT 1;\n")},
		{name: "valid plus malformed duplicate", data: []byte("-- evcowbbe:min-compatible-binary-version=0\n-- evcowbbe:min-compatible-binary-version=nope\nSELECT 1;\n")},
		{name: "malformed plus malformed duplicate", data: []byte("-- evcowbbe:min-compatible-binary-version=nope\n-- evcowbbe:min-compatible-binary-version=-1\nSELECT 1;\n")},
		{name: "extra trailing junk", data: []byte("-- evcowbbe:min-compatible-binary-version=0 trailing\nSELECT 1;\n")},
		{name: "above migration version", data: migrationSQL(2, "SELECT 1;\n")},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := LoadFiles(fstest.MapFS{"000001_example.sql": {Data: test.data}})
			if err == nil {
				t.Fatal("expected invalid compatibility metadata to be rejected")
			}
		})
	}
}

func TestLoadFilesAcceptsOneExactCompatibilityDirective(t *testing.T) {
	files := fstest.MapFS{
		"000001_example.sql": {Data: []byte("-- an ordinary SQL comment\n-- evcowbbe:min-compatible-binary-version=0\nSELECT 1;\n")},
	}

	migrations, err := LoadFiles(files)
	if err != nil {
		t.Fatalf("LoadFiles returned error: %v", err)
	}
	if len(migrations) != 1 || migrations[0].MinCompatibleBinaryVersion != 0 {
		t.Fatalf("unexpected migration metadata: %#v", migrations)
	}
}

func TestLoadFilesRejectsInvalidMigrationFiles(t *testing.T) {
	files := fstest.MapFS{"first.sql": {Data: migrationSQL(0, "SELECT 1;")}}
	if _, err := LoadFiles(files); err == nil {
		t.Fatal("expected invalid migration filename to be rejected")
	}
}

func TestBinarySchemaVersion(t *testing.T) {
	if got := BinarySchemaVersion(nil); got != 0 {
		t.Fatalf("expected zero-migration binary schema version 0, got %d", got)
	}
	if got := BinarySchemaVersion([]Migration{{Version: 1}, {Version: 7}, {Version: 3}}); got != 7 {
		t.Fatalf("expected highest migration version 7, got %d", got)
	}
}

func TestEvaluateAppliedRejectsKnownChecksumAndCompatibilityMismatches(t *testing.T) {
	manager := testManager(Migration{Version: 1, Name: "example", Checksum: "expected", MinCompatibleBinaryVersion: 0})

	checksumRecord := appliedMigration(1, "example", "different", 0)
	if _, err := manager.evaluateApplied(map[int64]AppliedMigration{1: checksumRecord}); !errors.Is(err, ErrChecksumMismatch) {
		t.Fatalf("expected checksum mismatch, got %v", err)
	}

	floorRecord := appliedMigration(1, "example", "expected", 1)
	if _, err := manager.evaluateApplied(map[int64]AppliedMigration{1: floorRecord}); !errors.Is(err, ErrCompatibilityMetadataMismatch) {
		t.Fatalf("expected compatibility metadata mismatch, got %v", err)
	}

	missingFloor := AppliedMigration{Version: 1, Name: "example", Checksum: "expected"}
	if _, err := manager.evaluateApplied(map[int64]AppliedMigration{1: missingFloor}); !errors.Is(err, ErrCompatibilityMetadataMismatch) {
		t.Fatalf("expected missing durable compatibility metadata to be rejected, got %v", err)
	}
}

func TestEvaluateAppliedRejectsUnknownHistoryAtOrBelowBinarySchemaVersion(t *testing.T) {
	manager := testManager(Migration{Version: 2, Name: "known", Checksum: "checksum", MinCompatibleBinaryVersion: 0})
	record := appliedMigration(1, "missing", "checksum", 0)

	if _, err := manager.evaluateApplied(map[int64]AppliedMigration{1: record}); !errors.Is(err, ErrMigrationHistoryConflict) {
		t.Fatalf("expected migration history conflict, got %v", err)
	}
}

func TestEvaluateAppliedClassifiesFutureCompatibility(t *testing.T) {
	manager := testManager(Migration{Version: 1, Name: "known", Checksum: "one", MinCompatibleBinaryVersion: 0})
	known := appliedMigration(1, "known", "one", 0)

	compatible := appliedMigration(2, "future", "two", 1)
	result, err := manager.evaluateApplied(map[int64]AppliedMigration{1: known, 2: compatible})
	if err != nil {
		t.Fatalf("expected compatible future migration to be accepted, got %v", err)
	}
	if len(result.FutureCompatible) != 1 || len(result.FutureIncompatible) != 0 {
		t.Fatalf("unexpected compatibility result: %#v", result)
	}

	incompatible := appliedMigration(2, "future", "two", 2)
	result, err = manager.evaluateApplied(map[int64]AppliedMigration{1: known, 2: incompatible})
	if err != nil {
		t.Fatalf("expected future migration classification, got %v", err)
	}
	if len(result.FutureIncompatible) != 1 {
		t.Fatalf("expected one incompatible future migration, got %#v", result)
	}
	if err := rejectIncompatibleFutureMigrations(result); !errors.Is(err, ErrFutureMigrationIncompatible) {
		t.Fatalf("expected incompatible future migration error, got %v", err)
	}

	malformed := appliedMigration(2, "future", "two", 3)
	if _, err := manager.evaluateApplied(map[int64]AppliedMigration{1: known, 2: malformed}); !errors.Is(err, ErrCompatibilityMetadataMismatch) {
		t.Fatalf("expected malformed durable future metadata to be rejected, got %v", err)
	}
}

func TestValidateReadyStateRejectsKnownPendingMigration(t *testing.T) {
	manager := testManager(Migration{Version: 1, Name: "known", Checksum: "one", MinCompatibleBinaryVersion: 0})
	if err := manager.validateReadyState(map[int64]AppliedMigration{}); !errors.Is(err, ErrMigrationPending) {
		t.Fatalf("expected pending migration to make readiness fail, got %v", err)
	}
}

func TestValidateReadyStateRejectsOutOfOrderFutureMigration(t *testing.T) {
	manager := testManager(Migration{Version: 1, Name: "known", Checksum: "one", MinCompatibleBinaryVersion: 0})
	future := appliedMigration(2, "future", "two", 1)

	if err := manager.validateReadyState(map[int64]AppliedMigration{2: future}); !errors.Is(err, ErrMigrationHistoryConflict) {
		t.Fatalf("expected out-of-order future migration to be rejected, got %v", err)
	}

	known := appliedMigration(1, "known", "one", 0)
	if err := manager.validateReadyState(map[int64]AppliedMigration{1: known, 2: future}); err != nil {
		t.Fatalf("expected applied known migration followed by compatible future migration to remain valid, got %v", err)
	}
}

func TestVerifyFiles(t *testing.T) {
	if err := VerifyFiles(); err != nil {
		t.Fatalf("VerifyFiles returned error: %v", err)
	}

	if got := Files(); len(got) != 0 {
		t.Fatalf("expected no application migrations yet, got %d", len(got))
	}
}

func TestValidateOptionalReleaseSHA(t *testing.T) {
	valid := "0123456789abcdef0123456789abcdef01234567"
	if err := validateOptionalReleaseSHA(""); err != nil {
		t.Fatalf("empty development release SHA returned error: %v", err)
	}
	if err := validateOptionalReleaseSHA(valid); err != nil {
		t.Fatalf("valid release SHA returned error: %v", err)
	}
	if err := validateOptionalReleaseSHA("dev"); err == nil {
		t.Fatal("expected invalid release SHA to be rejected")
	}
}

func migrationSQL(floor int64, statement string) []byte {
	return []byte(fmt.Sprintf("-- evcowbbe:min-compatible-binary-version=%d\n%s", floor, statement))
}

func testManager(migrations ...Migration) *Manager {
	return &Manager{migrations: migrations}
}

func appliedMigration(version int64, name, checksum string, floor int64) AppliedMigration {
	return AppliedMigration{
		Version:                    version,
		Name:                       name,
		Checksum:                   checksum,
		MinCompatibleBinaryVersion: &floor,
	}
}
