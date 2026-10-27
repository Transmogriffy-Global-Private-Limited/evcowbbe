package outbox

import (
	"bytes"
	"strings"
	"testing"
)

func TestEncryptedMailConfidentialityIntegrityAndIsolation(t *testing.T) {
	s := New(nil, []byte(strings.Repeat("p", 48)), nil)
	values := map[string]string{"Code": "TOP-SECRET-123", "Minutes": "20"}
	a, b, err := s.encrypt("a@example.test", "reset", values)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(a, []byte("TOP-SECRET")) {
		t.Fatal("plaintext credential leaked")
	}
	p, err := s.decrypt("a@example.test", "reset", a, b)
	if err != nil || p["Code"] != "TOP-SECRET-123" {
		t.Fatal("round-trip failed")
	}
	if _, err = s.decrypt("b@example.test", "reset", a, b); err == nil {
		t.Fatal("recipient substitution accepted")
	}
	if _, err = s.decrypt("a@example.test", "otp", a, b); err == nil {
		t.Fatal("template substitution accepted")
	}
	a[0] ^= 0x01
	if _, err = s.decrypt("a@example.test", "reset", a, b); err == nil {
		t.Fatal("tampered ciphertext accepted")
	}
	other := New(nil, []byte(strings.Repeat("q", 48)), nil)
	if _, err = other.decrypt("a@example.test", "reset", a, b); err == nil {
		t.Fatal("wrong key accepted")
	}
}

func TestOutboxIDAndBounds(t *testing.T) {
	a, err := randomUUID()
	if err != nil || len(a) != 36 || a[14] != '4' {
		t.Fatal("invalid generated UUID")
	}
	b, err := randomUUID()
	if err != nil || a == b {
		t.Fatal("UUID collision")
	}
	s := New(nil, []byte(strings.Repeat("p", 48)), nil)
	if _, _, err := s.encrypt("a@example.test", "otp", map[string]string{"Code": strings.Repeat("x", 5000)}); err == nil {
		t.Fatal("oversized secret payload accepted")
	}
}
