package api

import (
	"net/http/httptest"
	"testing"
)

func TestRemoteIPTrustBoundary(t *testing.T) {
	cases := []struct {
		name, peer, forwarded, want string
	}{
		{"direct IPv4 ignores spoofing", "203.0.113.8:4555", "198.51.100.9", "203.0.113.8"},
		{"direct IPv6 ignores spoofing", "[2001:db8::1]:4555", "198.51.100.9", "2001:db8::1"},
		{"proxied IPv4", "127.0.0.1:4555", "198.51.100.9", "198.51.100.9"},
		{"proxied IPv6", "[::1]:4555", "2001:db8::ab", "2001:db8::ab"},
		{"missing header", "127.0.0.1:4555", "", "127.0.0.1"},
		{"comma list rejected", "127.0.0.1:4555", "198.51.100.9, 203.0.113.8", "127.0.0.1"},
		{"unspecified address rejected", "127.0.0.1:4555", "0.0.0.0", "127.0.0.1"},
		{"loopback address rejected", "127.0.0.1:4555", "127.0.0.2", "127.0.0.1"},
		{"invalid address rejected", "127.0.0.1:4555", "not-an-ip", "127.0.0.1"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest("GET", "http://example.test/health/live", nil)
			r.RemoteAddr = tc.peer
			if tc.forwarded != "" {
				r.Header.Set("X-Real-IP", tc.forwarded)
			}
			if got := remoteIP(r); got != tc.want {
				t.Fatalf("remoteIP()=%q, want %q", got, tc.want)
			}
		})
	}

	t.Run("duplicate header rejected", func(t *testing.T) {
		r := httptest.NewRequest("GET", "http://example.test/health/live", nil)
		r.RemoteAddr = "127.0.0.1:4555"
		r.Header.Add("X-Real-IP", "198.51.100.9")
		r.Header.Add("X-Real-IP", "203.0.113.8")
		if got := remoteIP(r); got != "127.0.0.1" {
			t.Fatalf("duplicate X-Real-IP trusted: %q", got)
		}
	})
}
