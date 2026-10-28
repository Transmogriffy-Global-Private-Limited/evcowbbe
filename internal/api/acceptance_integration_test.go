package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	testPassword  = "A unique integration password 2026!"
	nextPassword  = "A different integration password 2026!"
	resetPassword = "A third integration password 2026!"
)

type fakeMail struct {
	mu       sync.Mutex
	messages []testMessage
	drain    func() error
}
type testMessage struct {
	to, kind string
	values   map[string]string
}

func (m *fakeMail) Send(_ context.Context, to, kind string, values map[string]string) error {
	clone := make(map[string]string, len(values))
	for k, v := range values {
		clone[k] = v
	}
	m.mu.Lock()
	m.messages = append(m.messages, testMessage{to: to, kind: kind, values: clone})
	m.mu.Unlock()
	return nil
}
func (m *fakeMail) code(t *testing.T, to, kind string) string {
	t.Helper()
	if m.drain != nil {
		if err := m.drain(); err != nil {
			t.Fatalf("outbox drain failed: %v", err)
		}
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	for i := len(m.messages) - 1; i >= 0; i-- {
		msg := m.messages[i]
		if msg.to == to && msg.kind == kind {
			if msg.values["Code"] == "" {
				t.Fatal("captured message is missing its code")
			}
			return msg.values["Code"]
		}
	}
	t.Fatalf("no captured %s message for test recipient", kind)
	return ""
}

type apiHarness struct {
	server *httptest.Server
	client *http.Client
	mail   *fakeMail
	auth   *auth.Service
}
type testEnvelope struct {
	Data  json.RawMessage `json:"data"`
	Error struct {
		Code string `json:"code"`
	} `json:"error"`
}

func testDBURL(t *testing.T) (string, bool) {
	t.Helper()
	appURL := os.Getenv("AUTH_TEST_APP_DATABASE_URL")
	raw := os.Getenv("TEST_DATABASE_URL")
	if appURL != "" {
		if raw != "" || os.Getenv("AUTH_TEST_EXISTING_DB_ACK") != "TEST_ONLY" {
			t.Fatal("existing-app database test requires exclusive, explicit TEST_ONLY authorization")
		}
		u, err := url.Parse(appURL)
		if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") ||
			u.Hostname() == "" || strings.Trim(u.Path, "/") == "" {
			t.Fatal("invalid existing-app database target")
		}
		return appURL, true
	}
	if raw == "" {
		t.Skip("TEST_DATABASE_URL absent: isolated PostgreSQL acceptance tests skipped")
	}
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") ||
		u.Hostname() == "" || strings.Count(u.Path, "/") != 1 ||
		!strings.HasSuffix(u.Path, "_test") {
		t.Fatal("TEST_DATABASE_URL must be a dedicated PostgreSQL database named *_test")
	}
	// This guard also catches a configured application DB ending in _test.
	if production := os.Getenv("DATABASE_URL"); production != "" {
		p, parseErr := url.Parse(production)
		if parseErr == nil && strings.EqualFold(u.Hostname(), p.Hostname()) &&
			u.Port() == p.Port() && u.Path == p.Path {
			t.Fatal("TEST_DATABASE_URL resolves to the application DATABASE_URL")
		}
	}
	return raw, false
}

func newAPIHarness(t *testing.T) *apiHarness {
	t.Helper()
	uri, existingAppDB := testDBURL(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	db, err := database.Open(ctx, uri, 8)
	if err != nil {
		t.Fatalf("isolated test database unavailable: %v", err)
	}
	t.Cleanup(db.Close)
	if existingAppDB {
		// Never run migrations on the existing application DB as part of tests.
		if err := database.VerifySchema(ctx, db); err != nil {
			t.Fatalf("application schema not ready; no test changes made: %v", err)
		}
		var adminCount int
		if err := db.QueryRow(ctx, "SELECT COUNT(*) FROM principals WHERE plane='admin'").Scan(&adminCount); err != nil || adminCount < 1 {
			t.Fatal("expected existing Admin principal not present; refusing existing-DB test writes")
		}
		adminBefore, err := adminCredentialsFingerprint(ctx, db)
		if err != nil {
			t.Fatal("cannot establish pre-test Admin account fingerprint")
		}
		t.Cleanup(func() {
			checkCtx, stop := context.WithTimeout(context.Background(), 8*time.Second)
			defer stop()
			adminAfter, checkErr := adminCredentialsFingerprint(checkCtx, db)
			if checkErr != nil {
				t.Error("cannot verify post-test Admin account fingerprint")
			} else if adminAfter != adminBefore {
				t.Error("existing Admin account attributes changed while running tests")
			}
		})
	} else {
		// Do not apply auth migrations if the *_test DB belongs to another app.
		rows, queryErr := db.Query(ctx, "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'")
		if queryErr != nil {
			t.Fatal("unable to inspect test database tables")
		}
		allowed := map[string]bool{
			"schema_migrations": true, "accounts": true, "principals": true,
			"auth_sessions": true, "consumed_refresh_tokens": true,
			"auth_challenges": true, "auth_action_tokens": true,
			"auth_rate_limits": true, "security_audit": true,
			"auth_mail_outbox": true, "admin_role_assignments": true,
			"admin_capability_overrides": true, "admin_rbac_audit": true,
			"service_vehicles": true, "chargers": true,
			"charger_connectors": true, "vehicle_charger_pairings": true,
			"asset_maintenance_periods": true, "fleet_asset_audit": true,
			"mutation_idempotency_receipts": true,
		}
		for rows.Next() {
			var table string
			if err := rows.Scan(&table); err != nil {
				rows.Close()
				t.Fatal("unable to read test database inventory")
			}
			if !allowed[table] {
				rows.Close()
				t.Fatal("test database contains unrelated application tables; refusing mutations")
			}
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			t.Fatal("test database inventory failed")
		}
		rows.Close()
		if err := database.Migrate(ctx, db, "up"); err != nil {
			t.Fatalf("isolated schema migration failed: %v", err)
		}
	}
	c := config.Config{
		Env: "development", PasswordPepper: []byte(strings.Repeat("p", 48)),
		OTPPepper: []byte(strings.Repeat("o", 48)), ArgonMemory: 65536,
		ArgonIterations: 3, ArgonParallelism: 1, AccessTTL: 15 * time.Minute,
		RefreshTTL: 30 * 24 * time.Hour, AbsoluteTTL: 90 * 24 * time.Hour,
		OTPTTL: 10 * time.Minute, OTPAttempts: 5, OTPResend: time.Minute,
		OTPPerHour: 30, AuthPerMinute: 200, RefreshPerMinute: 200,
	}
	svc := auth.New(db, c)
	mailer := &fakeMail{}
	svc.Mail = mailer
	mailer.drain = func() error {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		_, err := svc.DeliverPending(ctx, 100)
		return err
	}
	handler := (&Server{Auth: svc, DB: db, C: c}).Handler()
	// Do not share IP-based limiter keys with the actual localhost Admin login.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r.RemoteAddr = "192.0.2.250:40000"
		handler.ServeHTTP(w, r)
	}))
	t.Cleanup(server.Close)
	return &apiHarness{server: server, client: &http.Client{Timeout: 15 * time.Second}, mail: mailer, auth: svc}
}

func adminCredentialsFingerprint(ctx context.Context, db *pgxpool.Pool) (string, error) {
	var digest string
	err := db.QueryRow(ctx, "SELECT md5(COALESCE(string_agg(a.id::text || ':' || a.email || ':' || a.password_hash || ':' || a.two_factor_enabled::text || ':' || COALESCE(a.email_verified_at::text,'') || ':' || COALESCE(a.disabled_at::text,''), '|' ORDER BY a.id),'')) FROM accounts a JOIN principals p ON p.account_id=a.id WHERE p.plane='admin'").Scan(&digest)
	return digest, err
}

func uniqueTestEmail(t *testing.T, prefix string) string {
	t.Helper()
	id, err := security.UUID()
	if err != nil {
		t.Fatal(err)
	}
	return fmt.Sprintf("%s-%s@example.test", prefix, strings.ReplaceAll(id, "-", ""))
}

func (h *apiHarness) call(t *testing.T, method, path string, payload any, bearer string, want int) testEnvelope {
	t.Helper()
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			t.Fatalf("test request encoding failed: %v", err)
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, h.server.URL+path, body)
	if err != nil {
		t.Fatal(err)
	}
	if method == http.MethodPost || method == http.MethodPut || method == http.MethodDelete {
		req.Header.Set("Content-Type", "application/json")
	}
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	resp, err := h.client.Do(req)
	if err != nil {
		t.Fatalf("HTTP call failed: %v", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		t.Fatal("response read failed")
	}
	var result testEnvelope
	if err := json.Unmarshal(raw, &result); err != nil {
		t.Fatal("response envelope is invalid JSON")
	}
	if resp.StatusCode != want {
		// Deliberately never print response bodies or bearer tokens.
		t.Fatalf("%s %s: HTTP %d (error code %s), expected %d", method, path, resp.StatusCode, result.Error.Code, want)
	}
	if resp.Header.Get("Cache-Control") != "no-store" {
		t.Fatal("missing no-store on authentication response")
	}
	return result
}
func unpack[T any](t *testing.T, e testEnvelope) T {
	t.Helper()
	var result T
	if len(e.Data) == 0 {
		t.Fatal("response missing data")
	}
	if err := json.Unmarshal(e.Data, &result); err != nil {
		t.Fatal("response data shape mismatch")
	}
	return result
}
func (h *apiHarness) registerVerified(t *testing.T, plane, email, password string) {
	t.Helper()
	h.call(t, "POST", "/api/v1/"+plane+"/auth/register", map[string]string{"email": email, "password": password}, "", 202)
	verifyToken := h.mail.code(t, email, "verification")
	h.call(t, "POST", "/api/v1/auth/verify-email", map[string]string{"token": verifyToken}, "", 200)
	h.call(t, "POST", "/api/v1/auth/verify-email", map[string]string{"token": verifyToken}, "", 401)
}
func (h *apiHarness) login(t *testing.T, plane, email, password string) auth.LoginOutcome {
	t.Helper()
	e := h.call(t, "POST", "/api/v1/"+plane+"/auth/login", map[string]string{"email": email, "password": password}, "", 200)
	return unpack[auth.LoginOutcome](t, e)
}
func mustTokens(t *testing.T, outcome auth.LoginOutcome, plane string) auth.Tokens {
	t.Helper()
	if outcome.Tokens == nil || outcome.TwoFactorRequired ||
		outcome.Tokens.AccessToken == "" || outcome.Tokens.RefreshToken == "" ||
		outcome.Tokens.TokenType != "Bearer" || outcome.Tokens.Plane != plane {
		t.Fatal("login did not issue the correct plane-scoped access and refresh tokens")
	}
	return *outcome.Tokens
}

func TestIntegrationHTTPRegistrationAndRoleIsolation(t *testing.T) {
	h := newAPIHarness(t)
	customer := uniqueTestEmail(t, "app")
	driver := uniqueTestEmail(t, "driver")
	h.call(t, "POST", "/api/v1/admin/auth/register", map[string]string{"email": customer, "password": testPassword}, "", 403)
	h.call(t, "POST", "/api/v1/app/auth/register", map[string]string{"email": customer, "password": testPassword}, "", 202)
	h.call(t, "POST", "/api/v1/app/auth/login", map[string]string{"email": customer, "password": testPassword}, "", 403)
	h.call(t, "POST", "/api/v1/auth/verify-email", map[string]string{"token": h.mail.code(t, customer, "verification")}, "", 200)
	h.call(t, "POST", "/api/v1/app/auth/register", map[string]string{"email": customer, "password": testPassword}, "", 202)
	app := mustTokens(t, h.login(t, "app", customer, testPassword), "app")
	profile := unpack[auth.Profile](t, h.call(t, "GET", "/api/v1/app/me", nil, app.AccessToken, 200))
	if profile.Email != customer || profile.Plane != "app" || !profile.EmailVerified || profile.TwoFactorEnabled {
		t.Fatal("newly verified customer profile was incorrect")
	}
	h.call(t, "GET", "/api/v1/driver/me", nil, app.AccessToken, 403)
	h.call(t, "GET", "/api/v1/app/me", nil, "", 401)
	h.call(t, "POST", "/api/v1/admin/auth/login", map[string]string{"email": customer, "password": testPassword}, "", 401)
	h.call(t, "POST", "/api/v1/app/auth/login", map[string]string{"email": customer, "password": "not the correct password"}, "", 401)
	h.registerVerified(t, "driver", driver, testPassword)
	driverTokens := mustTokens(t, h.login(t, "driver", driver, testPassword), "driver")
	driverProfile := unpack[auth.Profile](t, h.call(t, "GET", "/api/v1/driver/me", nil, driverTokens.AccessToken, 200))
	if driverProfile.Plane != "driver" || driverProfile.PrincipalStatus != "PENDING" {
		t.Fatal("new driver unexpectedly received active driving status")
	}
	h.call(t, "GET", "/api/v1/admin/me", nil, driverTokens.AccessToken, 403)
	h.call(t, "POST", "/api/v1/auth/forgot-password", map[string]string{"email": uniqueTestEmail(t, "nonexistent")}, "", 200)
}

func TestIntegrationHTTPRefreshLogoutAndReplay(t *testing.T) {
	h := newAPIHarness(t)
	email := uniqueTestEmail(t, "sessions")
	h.registerVerified(t, "app", email, testPassword)
	first := mustTokens(t, h.login(t, "app", email, testPassword), "app")
	rotated := unpack[auth.Tokens](t, h.call(t, "POST", "/api/v1/app/auth/refresh", map[string]string{"refresh_token": first.RefreshToken}, "", 200))
	if rotated.AccessToken == first.AccessToken || rotated.RefreshToken == first.RefreshToken {
		t.Fatal("refresh did not rotate both tokens")
	}
	h.call(t, "GET", "/api/v1/app/me", nil, first.AccessToken, 401)
	h.call(t, "GET", "/api/v1/app/me", nil, rotated.AccessToken, 200)
	h.call(t, "POST", "/api/v1/app/auth/refresh", map[string]string{"refresh_token": first.RefreshToken}, "", 401)
	h.call(t, "GET", "/api/v1/app/me", nil, rotated.AccessToken, 401)
	next := mustTokens(t, h.login(t, "app", email, testPassword), "app")
	h.call(t, "POST", "/api/v1/app/auth/logout", nil, next.AccessToken, 200)
	h.call(t, "GET", "/api/v1/app/me", nil, next.AccessToken, 401)
	a := mustTokens(t, h.login(t, "app", email, testPassword), "app")
	b := mustTokens(t, h.login(t, "app", email, testPassword), "app")
	h.call(t, "POST", "/api/v1/app/auth/logout-all", nil, a.AccessToken, 200)
	h.call(t, "GET", "/api/v1/app/me", nil, a.AccessToken, 401)
	h.call(t, "GET", "/api/v1/app/me", nil, b.AccessToken, 401)
}

func TestIntegrationHTTPPasswordChangeAndRecovery(t *testing.T) {
	h := newAPIHarness(t)
	email := uniqueTestEmail(t, "recovery")
	h.registerVerified(t, "app", email, testPassword)
	old := mustTokens(t, h.login(t, "app", email, testPassword), "app")
	path := "/api/v1/app/auth/password/change"
	h.call(t, "POST", path, map[string]string{"current_password": "wrong password", "new_password": nextPassword}, old.AccessToken, 401)
	h.call(t, "POST", path, map[string]string{"current_password": testPassword, "new_password": nextPassword}, old.AccessToken, 200)
	h.call(t, "GET", "/api/v1/app/me", nil, old.AccessToken, 401)
	h.call(t, "POST", "/api/v1/app/auth/login", map[string]string{"email": email, "password": testPassword}, "", 401)
	next := mustTokens(t, h.login(t, "app", email, nextPassword), "app")
	h.call(t, "POST", "/api/v1/auth/forgot-password", map[string]string{"email": email}, "", 200)
	resetToken := h.mail.code(t, email, "reset")
	reset := map[string]string{"token": resetToken, "new_password": resetPassword}
	h.call(t, "POST", "/api/v1/auth/reset-password", reset, "", 200)
	h.call(t, "POST", "/api/v1/auth/reset-password", reset, "", 401)
	h.call(t, "GET", "/api/v1/app/me", nil, next.AccessToken, 401)
	h.call(t, "POST", "/api/v1/app/auth/login", map[string]string{"email": email, "password": nextPassword}, "", 401)
	mustTokens(t, h.login(t, "app", email, resetPassword), "app")
}

func TestIntegrationHTTPEmailOTPEnableLoginDisable(t *testing.T) {
	h := newAPIHarness(t)
	email := uniqueTestEmail(t, "otp")
	h.registerVerified(t, "app", email, testPassword)
	original := mustTokens(t, h.login(t, "app", email, testPassword), "app")
	begin := unpack[map[string]string](t, h.call(t, "POST", "/api/v1/app/auth/2fa/enable/begin", map[string]string{"password": testPassword}, original.AccessToken, 200))
	confirm := map[string]string{"challenge_id": begin["challenge_id"], "code": h.mail.code(t, email, "otp")}
	h.call(t, "POST", "/api/v1/app/auth/2fa/enable/confirm", confirm, original.AccessToken, 200)
	h.call(t, "POST", "/api/v1/app/auth/2fa/enable/confirm", confirm, original.AccessToken, 401)
	profile := unpack[auth.Profile](t, h.call(t, "GET", "/api/v1/app/me", nil, original.AccessToken, 200))
	if !profile.TwoFactorEnabled {
		t.Fatal("2FA not enabled after confirmation")
	}
	login := h.login(t, "app", email, testPassword)
	if !login.TwoFactorRequired || login.Tokens != nil || login.ChallengeID == "" {
		t.Fatal("enabled 2FA was bypassed")
	}
	code := h.mail.code(t, email, "otp")
	wrong := "999999"
	if code == wrong {
		wrong = "888888"
	}
	h.call(t, "POST", "/api/v1/app/auth/verify-otp", map[string]string{"challenge_id": login.ChallengeID, "code": wrong}, "", 401)
	verified := unpack[auth.Tokens](t, h.call(t, "POST", "/api/v1/app/auth/verify-otp", map[string]string{"challenge_id": login.ChallengeID, "code": code}, "", 200))
	h.call(t, "POST", "/api/v1/app/auth/verify-otp", map[string]string{"challenge_id": login.ChallengeID, "code": code}, "", 401)
	h.call(t, "GET", "/api/v1/app/me", nil, verified.AccessToken, 200)
	disable := unpack[map[string]string](t, h.call(t, "POST", "/api/v1/app/auth/2fa/disable/begin", map[string]string{"password": testPassword}, verified.AccessToken, 200))
	h.call(t, "POST", "/api/v1/app/auth/2fa/disable/confirm", map[string]string{"challenge_id": disable["challenge_id"], "code": h.mail.code(t, email, "otp")}, verified.AccessToken, 200)
	profile = unpack[auth.Profile](t, h.call(t, "GET", "/api/v1/app/me", nil, verified.AccessToken, 200))
	if profile.TwoFactorEnabled {
		t.Fatal("2FA remained enabled after disable confirmation")
	}
	mustTokens(t, h.login(t, "app", email, testPassword), "app")
}

func TestIntegrationHTTPRateLimitIsDatabaseCoordinated(t *testing.T) {
	h := newAPIHarness(t)
	id, err := security.UUID()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := h.auth.Limit(ctx, "acceptance-"+id, "isolated-key", time.Minute, 2); err != nil {
		t.Fatal(err)
	}
	if err := h.auth.Limit(ctx, "acceptance-"+id, "isolated-key", time.Minute, 2); err != nil {
		t.Fatal(err)
	}
	if err := h.auth.Limit(ctx, "acceptance-"+id, "isolated-key", time.Minute, 2); !errors.Is(err, auth.ErrRate) {
		t.Fatal("third request did not trigger the rate limit")
	}
}

func TestIntegrationHTTPConcurrentRefresh(t *testing.T) {
	h := newAPIHarness(t)
	email := uniqueTestEmail(t, "concurrent")
	h.registerVerified(t, "app", email, testPassword)
	first := mustTokens(t, h.login(t, "app", email, testPassword), "app")
	type result struct {
		tokens auth.Tokens
		err    error
	}
	observed := make(chan result, 2)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	for i := 0; i < 2; i++ {
		go func() {
			tokens, err := h.auth.Refresh(ctx, first.RefreshToken, "app")
			observed <- result{tokens: tokens, err: err}
		}()
	}
	var succeeded, revoked int
	var issued auth.Tokens
	for i := 0; i < 2; i++ {
		o := <-observed
		if o.err == nil {
			succeeded++
			issued = o.tokens
		} else if errors.Is(o.err, auth.ErrReused) {
			revoked++
		} else {
			t.Fatalf("unexpected concurrent refresh outcome: %v", o.err)
		}
	}
	if succeeded != 1 || revoked != 1 {
		t.Fatalf("concurrent refresh: success=%d reuse_revoked=%d", succeeded, revoked)
	}
	h.call(t, "GET", "/api/v1/app/me", nil, issued.AccessToken, 401)
}

func TestIntegrationAdminRBACAuthorizationAndAudit(t *testing.T) {
	if os.Getenv("AUTH_TEST_APP_DATABASE_URL") != "" {
		t.Skip("RBAC mutation acceptance runs only on isolated *_test database")
	}
	h := newAPIHarness(t)
	actorEmail := uniqueTestEmail(t, "rbac-actor")
	targetEmail := uniqueTestEmail(t, "rbac-target")
	if err := h.auth.BootstrapAdmin(context.Background(), actorEmail, testPassword); err != nil {
		t.Fatal(err)
	}
	if err := h.auth.BootstrapAdmin(context.Background(), targetEmail, testPassword); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	var actorID, targetID string
	if err := h.auth.Pool.QueryRow(ctx, `SELECT id FROM accounts WHERE email=$1`, actorEmail).Scan(&actorID); err != nil {
		t.Fatal(err)
	}
	if err := h.auth.Pool.QueryRow(ctx, `SELECT id FROM accounts WHERE email=$1`, targetEmail).Scan(&targetID); err != nil {
		t.Fatal(err)
	}
	actor := mustTokens(t, h.login(t, "admin", actorEmail, testPassword), "admin")
	target := mustTokens(t, h.login(t, "admin", targetEmail, testPassword), "admin")
	// Admin authentication grants no implicit role or capability.
	h.call(t, "GET", "/api/v1/admin/rbac/me", nil, actor.AccessToken, 200)
	h.call(t, "GET", "/api/v1/admin/rbac/catalog", nil, actor.AccessToken, 403)
	h.call(t, "POST", "/api/v1/admin/rbac/accounts/"+actorID+"/roles", map[string]string{"role": "FULL_ADMIN", "reason": "No authorization allowed"}, actor.AccessToken, 403)
	// Simulate explicitly trusted offline role bootstrap, only in isolated test DB.
	if _, err := h.auth.Pool.Exec(ctx, `INSERT INTO admin_role_assignments(account_id,role_key) VALUES($1,'FULL_ADMIN')`, actorID); err != nil {
		t.Fatal(err)
	}
	h.call(t, "GET", "/api/v1/admin/rbac/catalog", nil, actor.AccessToken, 200)
	path := "/api/v1/admin/rbac/accounts/" + targetID
	h.call(t, "GET", path, nil, actor.AccessToken, 200)
	h.call(t, "POST", path+"/roles", map[string]string{"role": "READ_ONLY", "reason": "Assign support visibility"}, actor.AccessToken, 200)
	h.call(t, "GET", "/api/v1/admin/rbac/catalog", nil, target.AccessToken, 200)
	h.call(t, "PUT", path+"/overrides/system.settings.read", map[string]string{"decision": "DENY", "reason": "Restrict settings metadata"}, actor.AccessToken, 200)
	h.call(t, "GET", "/api/v1/admin/rbac/catalog", nil, target.AccessToken, 403)
	h.call(t, "DELETE", path+"/overrides/system.settings.read", map[string]string{"reason": "Restore regular visibility"}, actor.AccessToken, 200)
	h.call(t, "GET", "/api/v1/admin/rbac/catalog", nil, target.AccessToken, 200)
	h.call(t, "POST", "/api/v1/admin/rbac/accounts/"+actorID+"/roles", map[string]string{"role": "READ_ONLY", "reason": "Self change prohibited"}, actor.AccessToken, 403)
	h.call(t, "DELETE", path+"/roles/READ_ONLY", map[string]string{"reason": "End temporary access scope"}, actor.AccessToken, 200)
	h.call(t, "GET", "/api/v1/admin/rbac/catalog", nil, target.AccessToken, 403)
	var auditCount int
	if err := h.auth.Pool.QueryRow(ctx, `SELECT COUNT(*) FROM admin_rbac_audit WHERE actor_id=$1 AND target_id=$2`, actorID, targetID).Scan(&auditCount); err != nil || auditCount != 4 {
		t.Fatalf("RBAC audit count %d, expected four valid mutations", auditCount)
	}
}
