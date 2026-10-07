package migrations

import (
	"errors"
	"testing"
	"testing/fstest"
)

func TestLoadFilesOrdersAndChecksumsMigrations(t *testing.T) {
	files := fstest.MapFS{
		"000010_second.sql": {Data: []byte("SELECT 2;\n")},
		"000002_first.sql":  {Data: []byte("SELECT 1;\n")},
	}

	migrations, err := LoadFiles(files)
	if err != nil {
		t.Fatalf("LoadFiles returned error: %v", err)
	}

	if len(migrations) != 2 {
		t.Fatalf("expected two migrations, got %d", len(migrations))
	}
	if migrations[0].Version != 2 || migrations[0].Name != "first" {
		t.Fatalf("unexpected first migration: %#v", migrations[0])
	}
	if migrations[0].Checksum == migrations[1].Checksum {
		t.Fatal("different SQL files must have different checksums")
	}
}

func TestLoadFilesRejectsInvalidMigrationFiles(t *testing.T) {
	tests := []struct {
		name  string
		files fstest.MapFS
	}{
		{
			name:  "invalid name",
			files: fstest.MapFS{"first.sql": {Data: []byte("SELECT 1;")}},
		},
		{
			name:  "empty SQL",
			files: fstest.MapFS{"000001_empty.sql": {Data: nil}},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := LoadFiles(test.files); err == nil {
				t.Fatal("expected invalid migration files to be rejected")
			}
		})
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

func TestVerifyAppliedDetectsChecksumMismatch(t *testing.T) {
	manager := &Manager{migrations: []Migration{{
		Version:  1,
		Name:     "example",
		Checksum: "expected",
	}}}

	err := manager.verifyApplied(map[int64]AppliedMigration{
		1: {Version: 1, Name: "example", Checksum: "different"},
	})
	if !errors.Is(err, ErrChecksumMismatch) {
		t.Fatalf("expected checksum mismatch, got %v", err)
	}
}
