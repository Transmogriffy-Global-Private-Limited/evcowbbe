package auth

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
)

type capturedEmail struct {
	mu   sync.Mutex
	last map[string]string
}

func (f *capturedEmail) Send(_ context.Context, _, _ string, p map[string]string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.last = map[string]string{}
	for k, v := range p {
		f.last[k] = v
	}
	return nil
}
func (f *capturedEmail) Code() string { f.mu.Lock(); defer f.mu.Unlock(); return f.last["Code"] }

// Run ONLY against a dedicated disposable PostgreSQL database with a name ending _test.
// e.g. TEST_DATABASE_URL=postgres://.../cow_auth_test?sslmode=disable go test -run Integration ./internal/auth -v
func TestIntegrationRegisterRefreshMFA(t *testing.T) {
	uri := os.Getenv("TEST_DATABASE_URL")
	if uri == "" {
		t.Skip("TEST_DATABASE_URL not set")
	}
	u, parseErr := url.Parse(uri)
	if parseErr != nil || !strings.HasSuffix(u.Path, "_test") || strings.Count(u.Path, "/") != 1 {
		t.Fatal("TEST_DATABASE_URL must name a dedicated database ending _test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 70*time.Second)
	defer cancel()
	c := config.Config{PasswordPepper: []byte(strings.Repeat("a", 48)), OTPPepper: []byte(strings.Repeat("b", 48)), ArgonMemory: 65536, ArgonIterations: 3, ArgonParallelism: 1, AccessTTL: 15 * time.Minute, RefreshTTL: 30 * 24 * time.Hour, AbsoluteTTL: 90 * 24 * time.Hour, OTPTTL: 10 * time.Minute, OTPAttempts: 5, OTPResend: time.Minute, OTPPerHour: 6}
	db, err := database.Open(ctx, uri, 10)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err = database.Migrate(ctx, db, "up"); err != nil {
		t.Fatal(err)
	}
	svc := New(db, c)
	box := &capturedEmail{}
	svc.Mail = box
	suffix, err := security.UUID()
	if err != nil {
		t.Fatal(err)
	}
	email := fmt.Sprintf("integration-%s@example.test", suffix)
	password := "A very long unique integration password #2026"
	if err = svc.Register(ctx, "app", email, password); err != nil {
		t.Fatal("register", err)
	}
	if _, err = svc.Login(ctx, "app", email, password); !errors.Is(err, ErrUnverified) {
		t.Fatal("unverified login should be blocked", err)
	}
	if err = svc.VerifyEmail(ctx, box.Code()); err != nil {
		t.Fatal("verify email", err)
	}
	result, err := svc.Login(ctx, "app", email, password)
	if err != nil || result.Tokens == nil {
		t.Fatal("login", err)
	}
	first := *result.Tokens
	if _, err = svc.Authenticate(ctx, first.AccessToken, "driver"); !errors.Is(err, ErrForbidden) {
		t.Fatal("cross-plane token accepted", err)
	}
	next, err := svc.Refresh(ctx, first.RefreshToken, "app")
	if err != nil {
		t.Fatal("rotate", err)
	}
	if next.RefreshToken == first.RefreshToken || next.AccessToken == first.AccessToken {
		t.Fatal("refresh did not rotate tokens")
	}
	if _, err = svc.Authenticate(ctx, first.AccessToken, "app"); err == nil {
		t.Fatal("stale access accepted")
	}
	if _, err = svc.Refresh(ctx, first.RefreshToken, "app"); !errors.Is(err, ErrReused) {
		t.Fatal("expected reuse revocation", err)
	}
	if _, err = svc.Authenticate(ctx, next.AccessToken, "app"); err == nil {
		t.Fatal("reused token family was not revoked")
	}
	result, err = svc.Login(ctx, "app", email, password)
	if err != nil {
		t.Fatal(err)
	}
	current := *result.Tokens
	principal, err := svc.Authenticate(ctx, current.AccessToken, "app")
	if err != nil {
		t.Fatal(err)
	}
	challenge, err := svc.Begin2FA(ctx, principal, password, true)
	if err != nil {
		t.Fatal("enable begin", err)
	}
	if err = svc.Confirm2FA(ctx, principal, challenge, box.Code(), true); err != nil {
		t.Fatal("enable confirm", err)
	}
	if err = svc.Logout(ctx, principal, false); err != nil {
		t.Fatal(err)
	}
	step, err := svc.Login(ctx, "app", email, password)
	if err != nil || !step.TwoFactorRequired || step.Tokens != nil {
		t.Fatal("two-factor login bypass", err)
	}
	badCode := "999999"
	if box.Code() == badCode {
		badCode = "888888"
	}
	if _, err = svc.VerifyLoginOTP(ctx, step.ChallengeID, badCode, "app"); !errors.Is(err, ErrInvalid) {
		t.Fatal("wrong OTP accepted", err)
	}
	otpCode := box.Code()

	final, err := svc.VerifyLoginOTP(ctx, step.ChallengeID, otpCode, "app")
	if err != nil {
		t.Fatal("OTP login", err)
	}
	if _, err = svc.VerifyLoginOTP(ctx, step.ChallengeID, otpCode, "app"); err == nil {
		t.Fatal("OTP replay accepted")
	}
	if _, err = svc.Authenticate(ctx, final.AccessToken, "app"); err != nil {
		t.Fatal("OTP-backed session invalid", err)
	}
}
