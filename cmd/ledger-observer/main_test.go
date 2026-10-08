package main

import (
	"encoding/json"
	"testing"
)

func TestEmptySnapshotIsExplicitNotNull(t *testing.T) {
	snapshot := ledgerSnapshot{LedgerPresent: true, Applied: make([]ledgerIdentity, 0)}
	data, err := json.Marshal(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	const expected = `{"ledger_present":true,"applied":[]}`
	if string(data) != expected {
		t.Fatalf("unexpected empty snapshot: %s", data)
	}
}

func TestLedgerIncludesNullableCompatibilityFloorWithoutGuessing(t *testing.T) {
	floor := int64(0)
	snapshot := ledgerSnapshot{LedgerPresent: true, Applied: []ledgerIdentity{
		{Version: 1, Name: "initial", Checksum: "example", MinCompatibleBinaryVersion: &floor},
		{Version: 2, Name: "missing_metadata", Checksum: "example", MinCompatibleBinaryVersion: nil},
	}}
	raw, err := json.Marshal(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	var observed struct {
		Applied []struct {
			Floor *int64 `json:"min_compatible_binary_version"`
		} `json:"applied"`
	}
	if err := json.Unmarshal(raw, &observed); err != nil {
		t.Fatal(err)
	}
	if len(observed.Applied) != 2 || observed.Applied[0].Floor == nil || *observed.Applied[0].Floor != 0 || observed.Applied[1].Floor != nil {
		t.Fatalf("nullable migration floor was lost: %s", raw)
	}
}
