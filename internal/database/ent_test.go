package database

import "testing"

func TestOpenEntClientRejectsNilPool(t *testing.T) {
	client, err := OpenEntClient(nil)
	if err == nil || client != nil {
		t.Fatalf("nil pool must fail closed: client=%v, err=%v", client, err)
	}
}
