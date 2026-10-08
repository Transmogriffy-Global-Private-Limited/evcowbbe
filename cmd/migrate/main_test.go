package main

import (
	"encoding/json"
	"testing"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/migrations"
)

func TestSnapshotLedgerPreservesExactAppliedIdentity(t *testing.T) {
	zero, one := int64(0), int64(1)
	status := migrations.Status{LedgerPresent: true,
		KnownApplied:     []migrations.AppliedMigration{{Version: 2, Name: "known", Checksum: "ab", MinCompatibleBinaryVersion: &zero}},
		FutureCompatible: []migrations.AppliedMigration{{Version: 3, Name: "future", Checksum: "cd", MinCompatibleBinaryVersion: &one}},
	}
	snapshot := snapshotLedger(status)
	if !snapshot.LedgerPresent || len(snapshot.Applied) != 2 || snapshot.Applied[0].Version != 2 || snapshot.Applied[1].Version != 3 || *snapshot.Applied[1].MinCompatibleBinaryVersion != 1 {
		t.Fatalf("incorrect applied ledger snapshot: %+v", snapshot)
	}
	encoded, err := json.Marshal(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	var decoded struct {
		LedgerPresent bool `json:"ledger_present"`
		Applied       []struct {
			Version                    int64  `json:"version"`
			MinCompatibleBinaryVersion *int64 `json:"min_compatible_binary_version"`
		} `json:"applied"`
	}
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	if !decoded.LedgerPresent || len(decoded.Applied) != 2 || *decoded.Applied[0].MinCompatibleBinaryVersion != 0 {
		t.Fatalf("JSON contract invalid: %s", encoded)
	}
}

func TestSnapshotLedgerMissingMetadataIsNotInvented(t *testing.T) {
	status := migrations.Status{LedgerPresent: true, KnownApplied: []migrations.AppliedMigration{{Version: 1, Name: "legacy"}}}
	snapshot := snapshotLedger(status)
	if len(snapshot.Applied) != 1 || snapshot.Applied[0].MinCompatibleBinaryVersion != nil {
		t.Fatalf("missing compatibility floor must remain nil: %+v", snapshot)
	}
	empty := snapshotLedger(migrations.Status{})
	if empty.LedgerPresent || empty.Applied == nil || len(empty.Applied) != 0 {
		t.Fatalf("missing ledger snapshot must not claim rows: %+v", empty)
	}
}
