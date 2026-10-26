package security

import "testing"

func TestTokensUnique(t *testing.T) {
	a, e := RandomToken()
	if e != nil {
		t.Fatal(e)
	}
	b, e := RandomToken()
	if e != nil || a == b || len(a) < 43 {
		t.Fatal("invalid token generation")
	}
}
func TestOTP(t *testing.T) {
	for i := 0; i < 200; i++ {
		o, e := OTP()
		if e != nil || len(o) != 6 {
			t.Fatal(o, e)
		}
		for _, c := range o {
			if c < '0' || c > '9' {
				t.Fatal(o)
			}
		}
	}
}
func TestArgon(t *testing.T) {
	h, e := HashPassword("unpredictable password 2026", []byte("a-safe-test-pepper-not-for-production"), 65536, 3, 1)
	if e != nil {
		t.Fatal(e)
	}
	ok, e := CheckPassword("unpredictable password 2026", h, []byte("a-safe-test-pepper-not-for-production"))
	if e != nil || !ok {
		t.Fatal(e)
	}
	ok, _ = CheckPassword("wrong", h, []byte("a-safe-test-pepper-not-for-production"))
	if ok {
		t.Fatal("bad password verified")
	}
}
func TestEmail(t *testing.T) {
	if _, err := Email("a@b.com\nBcc:x"); err == nil {
		t.Fatal("header injection accepted")
	}
	x, e := Email("Person@Example.COM")
	if e != nil || x != "person@example.com" {
		t.Fatal(x, e)
	}
}
