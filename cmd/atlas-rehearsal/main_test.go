package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDisposableURLBoundary(t *testing.T) {
	accepted := []string{
		"postgres://test:password@localhost:5432/evcowbbe_atlas_populated_20261029_test?sslmode=disable",
		"postgresql://test:password@10.1.2.3/evcowbbe_atlas_fresh_test",
	}
	for _, raw := range accepted {
		if _, err := checkTarget(raw, "postgres://live:secret@localhost/evcowbbe"); err != nil {
			t.Fatalf("rejected disposable URL: %v", err)
		}
	}
	rejected := []string{
		"", "postgres://test:password@localhost/evcowbbe",
		"postgres://test:password@localhost/evcowbbe_restore_f3_20261028082255_31c7b2e2",
		"postgres://test:password@localhost/evcowbbe_atlas_fresh_test?dbname=evcowbbe",
		"postgres://test:password@localhost/evcowbbe_atlas_fresh_test/other",
		"postgres://test:password@localhost/evcowbbe_atlas_fresh_test%2fother",
		"postgres://test:password@localhost/evcowbbe_atlas_fresh_test?database=evcowbbe",
		"postgres://localhost/evcowbbe_atlas_fresh_test",
		"mysql://test:password@localhost/evcowbbe_atlas_fresh_test",
	}
	for _, raw := range rejected {
		if _, err := checkTarget(raw, "postgres://test:password@localhost/evcowbbe_atlas_fresh_test"); err == nil {
			t.Fatalf("accepted unsafe URL: %s", raw)
		}
	}
	if err := applyLegacy(t.Context(), accepted[0], "", ""); err == nil || !strings.Contains(err.Error(), "consent") {
		t.Fatalf("legacy runner must refuse without consent: %v", err)
	}
	if err := applyLegacy(t.Context(), "postgres://test:password@localhost/evcowbbe_atlas_dev_test", "", legacyUpConsent); err == nil || !strings.Contains(err.Error(), "populated") {
		t.Fatalf("legacy runner accepted a scratch dev database: %v", err)
	}
	if err := checkEmpty(t.Context(), accepted[0], "", "fresh"); err == nil || !strings.Contains(err.Error(), "target class") {
		t.Fatalf("fresh preflight accepted populated database: %v", err)
	}
}

func sample(db string) *snapshot {
	history := make(map[string]string)
	for k, v := range legacyChecksums {
		history[k] = v
	}
	return &snapshot{
		Database: db, ServerVersion: 180004, History: history, AtlasRevisions: 1,
		Catalog: []string{`["column","accounts","email"]`, `["table","accounts"]`},
		Tables:  map[string]tableDigest{"accounts": {Rows: 2, SHA256: "abc"}},
	}
}

func TestDataFingerprintAndComparison(t *testing.T) {
	a := sha256.New()
	digestRow(a, "a")
	digestRow(a, "bc")
	b := sha256.New()
	digestRow(b, "ab")
	digestRow(b, "c")
	if hex.EncodeToString(a.Sum(nil)) == hex.EncodeToString(b.Sum(nil)) {
		t.Fatal("row length framing must prevent concatenation collisions")
	}
	before := sample("evcowbbe_atlas_restore_test")
	delete(before.History, "6")
	after := sample(before.Database)
	after.Tables["customer_profiles"] = tableDigest{SHA256: "ef", Rows: 0}
	if err := compareData(before, after); err != nil {
		t.Fatalf("additive migration wrongly flagged: %v", err)
	}
	before.Tables["accounts"] = tableDigest{Rows: 3, SHA256: "broken"}
	if err := compareData(before, after); err == nil {
		t.Fatal("data drift not detected")
	}
	before.Tables["accounts"] = after.Tables["accounts"]
	if err := compareData(before, sample("evcowbbe_atlas_other_test")); err == nil {
		t.Fatal("cross-DB data comparison accepted")
	}
	delete(after.History, "2")
	if err := compareData(before, after); err == nil {
		t.Fatal("historical ledger loss not detected")
	}
}

func TestSchemaParity(t *testing.T) {
	a := sample("evcowbbe_atlas_restore_test")
	b := sample("evcowbbe_atlas_fresh_test")
	if err := compareSchema(a, b); err != nil {
		t.Fatal(err)
	}
	b.Catalog[0] = `["column","accounts","email","collation_drift"]`
	if err := compareSchema(a, b); err == nil {
		t.Fatal("column drift accepted")
	}
	b = sample("evcowbbe_atlas_fresh_test")
	b.History["5"] = "wrong"
	if err := compareSchema(a, b); err == nil {
		t.Fatal("migration ledger drift accepted")
	}
	b = sample("evcowbbe_atlas_fresh_test")
	b.AtlasRevisions = 0
	if err := compareSchema(a, b); err == nil {
		t.Fatal("missing Atlas revision was accepted")
	}
	if err := validHistory(a); err != nil {
		t.Fatal(err)
	}
}

func TestSnapshotJSONStrictness(t *testing.T) {
	path := filepath.Join(t.TempDir(), "snapshot.json")
	b, err := json.Marshal(sample("evcowbbe_atlas_restore_test"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readSnapshot(path); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, bytes.Join([][]byte{b, b}, []byte("\n")), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readSnapshot(path); err == nil {
		t.Fatal("accepted concatenated snapshots")
	}
}
