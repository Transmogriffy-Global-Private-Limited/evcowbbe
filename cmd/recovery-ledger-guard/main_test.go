package main

import (
	"os"
	"strings"
	"testing"
)

func TestNoArgumentsAccepted(t *testing.T) {
	original := os.Args
	defer func() { os.Args = original }()
	os.Args = []string{"recovery-ledger-guard", "unexpected"}
	if err := run(); err == nil || !strings.Contains(err.Error(), "no arguments") {
		t.Fatal("guard must reject all caller-supplied arguments")
	}
}

func TestDatabaseCredentialIsRequired(t *testing.T) {
	original := os.Args
	defer func() { os.Args = original }()
	os.Args = []string{"recovery-ledger-guard"}
	t.Setenv("DATABASE_URL", "")
	if err := run(); err == nil || !strings.Contains(err.Error(), "credential") {
		t.Fatal("guard must refuse an absent fixed EnvironmentFile credential")
	}
}

func TestEmptyLedgerFingerprintIsPinned(t *testing.T) {
	if len(expectedEmptySHA256) != 64 || expectedEmptySHA256 !=
		"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945" {
		t.Fatal("pinned ledger fingerprint changed")
	}
	if migrationAdvisoryLockID != 44727018461933121 {
		t.Fatal("migration advisory lock identity changed")
	}
}
