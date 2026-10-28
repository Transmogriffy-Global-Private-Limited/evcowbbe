package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
)

// End-to-end HTTP tests execute only on a dedicated disposable *_test DB.
// Never bootstrap an Admin or grant capabilities against the VPS/staging DB.
func TestIntegrationAdminFleetRoutes(t *testing.T) {
	if os.Getenv("AUTH_TEST_APP_DATABASE_URL") != "" {
		t.Skip("fleet write tests require disposable *_test DB")
	}
	h := newAPIHarness(t)
	email := uniqueTestEmail(t, "fleet-admin")
	if err := h.auth.BootstrapAdmin(context.Background(), email, testPassword); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	var adminID string
	if err := h.auth.Pool.QueryRow(ctx, `SELECT id FROM accounts WHERE email=$1`, email).Scan(&adminID); err != nil {
		t.Fatal(err)
	}
	token := mustTokens(t, h.login(t, "admin", email, testPassword), "admin").AccessToken
	base := "/api/v1/admin/vehicles"
	h.call(t, "GET", base, nil, token, 403) // Admin identity alone does not grant Fleet read.
	if _, err := h.auth.Pool.Exec(ctx, `INSERT INTO admin_role_assignments(account_id,role_key) VALUES($1,'FULL_ADMIN')`, adminID); err != nil {
		t.Fatal(err)
	}
	// Direct caller supplies strict HTTP headers missing from generic auth helper.
	call := func(method, path, body, key, match string, want int) (testEnvelope, string) {
		t.Helper()
		req, err := http.NewRequest(method, h.server.URL+path, bytes.NewBufferString(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		if body != "" {
			req.Header.Set("Content-Type", "application/json")
		}
		if key != "" {
			req.Header.Set("Idempotency-Key", key)
		}
		if match != "" {
			req.Header.Set("If-Match", match)
		}
		resp, err := h.client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		if err != nil {
			t.Fatal(err)
		}
		var e testEnvelope
		if err = json.Unmarshal(raw, &e); err != nil {
			t.Fatal(err)
		}
		if resp.StatusCode != want {
			t.Fatalf("%s %s: HTTP %d code %s, want %d", method, path, resp.StatusCode, e.Error.Code, want)
		}
		return e, resp.Header.Get("ETag")
	}
	uid, err := security.UUID()
	if err != nil {
		t.Fatal(err)
	}
	key := "create-vehicle-" + uid
	body := fmt.Sprintf(`{"asset_tag":"SV-%s","make":"Tata","model":"Ace"}`, uid[:8])
	call("POST", base, body, "", "", 428)
	created, etag := call("POST", base, body, key, "", 201)
	if etag != "\"1\"" {
		t.Fatalf("create missing ETag: %q", etag)
	}
	var v struct {
		ID      string `json:"id"`
		Version int64  `json:"version"`
	}
	if err = json.Unmarshal(created.Data, &v); err != nil || v.ID == "" {
		t.Fatalf("invalid create result: %v", err)
	}
	replay, _ := call("POST", base, body, key, "", 201)
	if !bytes.Equal(replay.Data, created.Data) {
		t.Fatal("replayed result changed")
	}
	call("POST", base, `{"asset_tag":"OTHER","make":"Tata","model":"Ace"}`, key, "", 409)
	_, etag = call("PATCH", base+"/"+v.ID, `{"notes":"Fleet inventory approved"}`, "patch-"+uid, "", 428)
	patched, etag := call("PATCH", base+"/"+v.ID, `{"notes":"Fleet inventory approved"}`, "patch-"+uid, "\"1\"", 200)
	if etag != "\"2\"" {
		t.Fatalf("patch did not advance ETag: %q", etag)
	}
	if !bytes.Contains(patched.Data, []byte("Fleet inventory approved")) {
		t.Fatal("patch not persisted")
	}
	call("PATCH", base+"/"+v.ID, `{"notes":"Stale edit"}`, "stale-"+uid, "\"1\"", 412)
	call("GET", base+"/"+v.ID, "", "", "", 200)
	call("GET", base+"?limit=1", "", "", "", 200)
	call("GET", base+"/"+v.ID+"/history", "", "", "", 200)
	call("GET", base+"/"+v.ID+"/availability", "", "", "", 200)
	// Revocation takes effect on the next request; no role-name shortcut.
	if _, err := h.auth.Pool.Exec(ctx, `DELETE FROM admin_role_assignments WHERE account_id=$1`, adminID); err != nil {
		t.Fatal(err)
	}
	call("GET", base+"/"+v.ID, "", "", "", 403)
}
