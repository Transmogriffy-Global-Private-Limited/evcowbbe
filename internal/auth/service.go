package auth

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/mailer"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrInvalid    = errors.New("invalid credentials or token")
	ErrConflict   = errors.New("already exists")
	ErrForbidden  = errors.New("access forbidden")
	ErrUnverified = errors.New("email verification required")
	ErrRate       = errors.New("too many requests")
	ErrCooldown   = errors.New("resend cooldown")
	ErrDependency = errors.New("service temporarily unavailable")
	ErrExpired    = errors.New("challenge expired or exhausted")
	ErrReused     = errors.New("refresh token reused; session revoked")
)

// Direct pgxpool usage maintains transparent SQL and explicit transaction boundaries.
type Mailer interface {
	Send(context.Context, string, string, map[string]string) error
}
type Service struct {
	Pool *pgxpool.Pool
	C    config.Config
	Mail Mailer
}

func New(pool *pgxpool.Pool, c config.Config) *Service {
	return &Service{Pool: pool, C: c, Mail: mailer.Sender{Cfg: c}}
}

type Tokens struct {
	AccessToken      string    `json:"access_token"`
	RefreshToken     string    `json:"refresh_token"`
	TokenType        string    `json:"token_type"`
	AccessExpiresAt  time.Time `json:"access_expires_at"`
	RefreshExpiresAt time.Time `json:"refresh_expires_at"`
	SessionID        string    `json:"session_id"`
	Plane            string    `json:"plane"`
}
type LoginOutcome struct {
	Tokens            *Tokens `json:"tokens,omitempty"`
	TwoFactorRequired bool    `json:"two_factor_required"`
	ChallengeID       string  `json:"challenge_id,omitempty"`
}
type Profile struct {
	ID               string `json:"id"`
	Email            string `json:"email"`
	Plane            string `json:"plane"`
	PrincipalStatus  string `json:"principal_status"`
	EmailVerified    bool   `json:"email_verified"`
	TwoFactorEnabled bool   `json:"two_factor_enabled"`
}
type Principal struct{ AccountID, SessionID, Plane string }

func ValidPlane(s string) bool { return s == "app" || s == "driver" || s == "admin" }

type rateQuerier interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}

func (s *Service) Limit(ctx context.Context, bucket, key string, period time.Duration, max int) error {
	return s.limitWith(ctx, s.Pool, bucket, key, period, max)
}
func (s *Service) limitTx(ctx context.Context, tx pgx.Tx, bucket, key string, period time.Duration, max int) error {
	return s.limitWith(ctx, tx, bucket, key, period, max)
}
func (s *Service) limitWith(ctx context.Context, q rateQuerier, bucket, key string, period time.Duration, max int) error {
	if bucket == "" || key == "" {
		return ErrInvalid
	}
	digest := security.Mac(s.C.OTPPepper, "limit", bucket, key)
	// Database-coordinated fixed window: safe for multiple API instances.
	var hits int
	err := q.QueryRow(ctx, `INSERT INTO auth_rate_limits(bucket,key_digest,window_start,hits)
 VALUES($1,$2,now(),1)
 ON CONFLICT(bucket,key_digest) DO UPDATE SET
 hits=CASE WHEN auth_rate_limits.window_start <= now()-$3::interval THEN 1 ELSE auth_rate_limits.hits+1 END,
 window_start=CASE WHEN auth_rate_limits.window_start <= now()-$3::interval THEN now() ELSE auth_rate_limits.window_start END
 RETURNING hits`, bucket, digest, fmt.Sprintf("%d seconds", int(period.Seconds()))).Scan(&hits)
	if err != nil {
		return ErrDependency
	}
	if hits > max {
		return ErrRate
	}
	return nil
}
func issueToken() (string, []byte, error) {
	v, err := security.RandomToken()
	if err != nil {
		return "", nil, err
	}
	return v, security.Digest(v), nil
}
func uuid() (string, error) { return security.UUID() }
func (s *Service) newSession(ctx context.Context, tx pgx.Tx, account, plane string) (Tokens, error) {
	id, err := uuid()
	if err != nil {
		return Tokens{}, err
	}
	at, ad, err := issueToken()
	if err != nil {
		return Tokens{}, err
	}
	rt, rd, err := issueToken()
	if err != nil {
		return Tokens{}, err
	}
	now := time.Now().UTC()
	aexp := now.Add(s.C.AccessTTL)
	rexp := now.Add(s.C.RefreshTTL)
	abs := now.Add(s.C.AbsoluteTTL)
	_, err = tx.Exec(ctx, `INSERT INTO auth_sessions(id,account_id,plane,access_digest,refresh_digest,access_expires_at,refresh_expires_at,absolute_expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, id, account, plane, ad, rd, aexp, rexp, abs)
	return Tokens{AccessToken: at, RefreshToken: rt, TokenType: "Bearer", AccessExpiresAt: aexp, RefreshExpiresAt: rexp, SessionID: id, Plane: plane}, err
}
func audit(ctx context.Context, tx pgx.Tx, account, plane, event string) error {
	_, err := tx.Exec(ctx, `INSERT INTO security_audit(account_id,plane,event) VALUES($1,$2,$3)`, account, plane, event)
	return err
}

func (s *Service) Register(ctx context.Context, plane, email, password string) error {
	if plane != "app" && plane != "driver" {
		return ErrForbidden
	}
	e, err := security.Email(email)
	if err != nil {
		return ErrInvalid
	}
	if err = security.PasswordValid(password); err != nil {
		return err
	}
	hash, err := security.HashPassword(password, s.C.PasswordPepper, s.C.ArgonMemory, s.C.ArgonIterations, s.C.ArgonParallelism)
	if err != nil {
		return ErrDependency
	}
	id, err := uuid()
	if err != nil {
		return ErrDependency
	}
	token, digest, err := issueToken()
	if err != nil {
		return ErrDependency
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	var exists bool
	_ = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM accounts WHERE email=$1)`, e).Scan(&exists)
	if exists {
		return ErrConflict
	}
	_, err = tx.Exec(ctx, `INSERT INTO accounts(id,email,password_hash) VALUES($1,$2,$3)`, id, e, hash)
	if err != nil {
		return ErrConflict
	}
	state := "ACTIVE"
	if plane == "driver" {
		state = "PENDING"
	}
	if _, err = tx.Exec(ctx, `INSERT INTO principals(account_id,plane,status) VALUES($1,$2,$3)`, id, plane, state); err != nil {
		return ErrDependency
	}
	if _, err = tx.Exec(ctx, `INSERT INTO auth_action_tokens(digest,account_id,kind,expires_at) VALUES($1,$2,'VERIFY_EMAIL',now()+interval '24 hours')`, digest, id); err != nil {
		return ErrDependency
	}
	if err = audit(ctx, tx, id, plane, "ACCOUNT_REGISTERED"); err != nil {
		return ErrDependency
	}
	if err = s.Mail.Send(ctx, e, "verification", map[string]string{"Code": token, "Minutes": "1440"}); err != nil {
		return ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return ErrDependency
	}
	return nil
}
func (s *Service) VerifyEmail(ctx context.Context, token string) error {
	if len(token) != 43 {
		return ErrInvalid
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	var aid string
	err = tx.QueryRow(ctx, `UPDATE auth_action_tokens SET consumed_at=now() WHERE digest=$1 AND kind='VERIFY_EMAIL' AND consumed_at IS NULL AND expires_at>now() RETURNING account_id`, security.Digest(token)).Scan(&aid)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrInvalid
	}
	if err != nil {
		return ErrDependency
	}
	_, err = tx.Exec(ctx, `UPDATE accounts SET email_verified_at=COALESCE(email_verified_at,now()),updated_at=now() WHERE id=$1`, aid)
	if err != nil {
		return ErrDependency
	}
	if err = audit(ctx, tx, aid, "", "EMAIL_VERIFIED"); err != nil {
		return ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return ErrDependency
	}
	return nil
}
func (s *Service) ResendVerification(ctx context.Context, email string) error {
	e, err := security.Email(email)
	if err != nil {
		return nil
	}
	if err = s.Limit(ctx, "verification", e, time.Hour, s.C.OTPPerHour); err != nil {
		return err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	var aid string
	err = tx.QueryRow(ctx, `SELECT id FROM accounts WHERE email=$1 AND email_verified_at IS NULL AND disabled_at IS NULL FOR UPDATE`, e).Scan(&aid)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return ErrDependency
	}
	token, digest, err := issueToken()
	if err != nil {
		return ErrDependency
	}
	_, err = tx.Exec(ctx, `DELETE FROM auth_action_tokens WHERE account_id=$1 AND kind='VERIFY_EMAIL'`, aid)
	if err != nil {
		return ErrDependency
	}
	_, err = tx.Exec(ctx, `INSERT INTO auth_action_tokens(digest,account_id,kind,expires_at) VALUES($1,$2,'VERIFY_EMAIL',now()+interval '24 hours')`, digest, aid)
	if err != nil {
		return ErrDependency
	}
	if err = s.Mail.Send(ctx, e, "verification", map[string]string{"Code": token, "Minutes": "1440"}); err != nil {
		return ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return ErrDependency
	}
	return nil
}
func (s *Service) Login(ctx context.Context, plane, email, password string) (LoginOutcome, error) {
	if !ValidPlane(plane) {
		return LoginOutcome{}, ErrForbidden
	}
	e, err := security.Email(email)
	if err != nil {
		return LoginOutcome{}, ErrInvalid
	}
	var id, hash, status string
	var enabled, verified, disabled bool
	err = s.Pool.QueryRow(ctx, `SELECT a.id,a.password_hash,p.status,a.two_factor_enabled,a.email_verified_at IS NOT NULL,a.disabled_at IS NOT NULL FROM accounts a JOIN principals p ON p.account_id=a.id WHERE a.email=$1 AND p.plane=$2`, e, plane).Scan(&id, &hash, &status, &enabled, &verified, &disabled)
	if errors.Is(err, pgx.ErrNoRows) {
		_, _ = security.HashPassword(password, s.C.PasswordPepper, s.C.ArgonMemory, s.C.ArgonIterations, s.C.ArgonParallelism)
		return LoginOutcome{}, ErrInvalid
	}
	if err != nil {
		return LoginOutcome{}, ErrDependency
	}
	ok, err := security.CheckPassword(password, hash, s.C.PasswordPepper)
	if err != nil {
		return LoginOutcome{}, ErrDependency
	}
	if !ok || disabled || status == "SUSPENDED" {
		return LoginOutcome{}, ErrInvalid
	}
	if !verified {
		return LoginOutcome{}, ErrUnverified
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return LoginOutcome{}, ErrDependency
	}
	defer tx.Rollback(ctx)
	// Lock account; prevents racing 2FA state changes and session creation.
	err = tx.QueryRow(ctx, `SELECT two_factor_enabled FROM accounts WHERE id=$1 AND disabled_at IS NULL FOR UPDATE`, id).Scan(&enabled)
	if err != nil {
		return LoginOutcome{}, ErrInvalid
	}
	if enabled {
		if err = s.limitTx(ctx, tx, "otp_send", id, time.Hour, s.C.OTPPerHour); err != nil {
			return LoginOutcome{}, err
		}
		challenge, err := s.makeChallenge(ctx, tx, id, e, plane, "LOGIN_2FA")
		if err != nil {
			return LoginOutcome{}, err
		}
		if err = tx.Commit(ctx); err != nil {
			return LoginOutcome{}, ErrDependency
		}
		return LoginOutcome{TwoFactorRequired: true, ChallengeID: challenge}, nil
	}
	tokens, err := s.newSession(ctx, tx, id, plane)
	if err != nil {
		return LoginOutcome{}, ErrDependency
	}
	if err = audit(ctx, tx, id, plane, "LOGIN_SUCCEEDED"); err != nil {
		return LoginOutcome{}, ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return LoginOutcome{}, ErrDependency
	}
	return LoginOutcome{Tokens: &tokens}, nil
}
func (s *Service) makeChallenge(ctx context.Context, tx pgx.Tx, account, email, plane, purpose string) (string, error) {
	id, err := uuid()
	if err != nil {
		return "", ErrDependency
	}
	code, err := security.OTP()
	if err != nil {
		return "", ErrDependency
	}
	if _, err = tx.Exec(ctx, `UPDATE auth_challenges SET consumed_at=now() WHERE account_id=$1 AND purpose=$2 AND consumed_at IS NULL`, account, purpose); err != nil {
		return "", ErrDependency
	}
	digest := security.Mac(s.C.OTPPepper, "otp", purpose, id, code)
	if _, err = tx.Exec(ctx, `INSERT INTO auth_challenges(id,account_id,plane,purpose,digest,expires_at) VALUES($1,$2,$3,$4,$5,now()+$6::interval)`, id, account, plane, purpose, digest, fmt.Sprintf("%d seconds", int(s.C.OTPTTL.Seconds()))); err != nil {
		return "", ErrDependency
	}
	// Fail closed: if email cannot be handed off, entire new challenge rolls back.
	if err = s.Mail.Send(ctx, email, "otp", map[string]string{"Code": code, "Action": strings.ReplaceAll(purpose, "_", " "), "Minutes": fmt.Sprint(int(s.C.OTPTTL.Minutes()))}); err != nil {
		return "", ErrDependency
	}
	return id, nil
}
func (s *Service) VerifyLoginOTP(ctx context.Context, challengeID, code, requestedPlane string) (Tokens, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return Tokens{}, ErrDependency
	}
	defer tx.Rollback(ctx)
	account, plane, err := s.checkChallenge(ctx, tx, challengeID, code, "LOGIN_2FA")
	if err != nil {
		if errors.Is(err, ErrInvalid) {
			if commitErr := tx.Commit(ctx); commitErr != nil {
				return Tokens{}, ErrDependency
			}
		}
		return Tokens{}, err
	}
	if plane != requestedPlane {
		return Tokens{}, ErrForbidden
	}
	var enabled bool
	err = tx.QueryRow(ctx, `SELECT two_factor_enabled FROM accounts WHERE id=$1 AND disabled_at IS NULL AND email_verified_at IS NOT NULL FOR UPDATE`, account).Scan(&enabled)
	if err != nil || !enabled {
		return Tokens{}, ErrForbidden
	}
	var status string
	err = tx.QueryRow(ctx, `SELECT status FROM principals WHERE account_id=$1 AND plane=$2`, account, plane).Scan(&status)
	if err != nil || status == "SUSPENDED" {
		return Tokens{}, ErrForbidden
	}
	tokens, err := s.newSession(ctx, tx, account, plane)
	if err != nil {
		return Tokens{}, ErrDependency
	}
	if err = audit(ctx, tx, account, plane, "LOGIN_2FA_SUCCEEDED"); err != nil {
		return Tokens{}, ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return Tokens{}, ErrDependency
	}
	return tokens, nil
}

// checkChallenge atomically consumes a correct code or increments bad attempts.
func (s *Service) checkChallenge(ctx context.Context, tx pgx.Tx, id, code, purpose string) (string, string, error) {
	if len(code) != 6 || len(id) != 36 {
		return "", "", ErrInvalid
	}
	var account, plane string
	var digest []byte
	var tries int
	var expiry time.Time
	var consumed *time.Time
	err := tx.QueryRow(ctx, `SELECT account_id,plane,digest,attempts,expires_at,consumed_at FROM auth_challenges WHERE id=$1 AND purpose=$2 FOR UPDATE`, id, purpose).Scan(&account, &plane, &digest, &tries, &expiry, &consumed)
	if err != nil {
		return "", "", ErrInvalid
	}
	if consumed != nil || !expiry.After(time.Now()) || tries >= s.C.OTPAttempts {
		return "", "", ErrExpired
	}
	if !security.Equal(digest, security.Mac(s.C.OTPPepper, "otp", purpose, id, code)) {
		_, err = tx.Exec(ctx, `UPDATE auth_challenges SET attempts=attempts+1 WHERE id=$1`, id)
		if err != nil {
			return "", "", ErrDependency
		}
		return "", "", ErrInvalid
	}
	if _, err = tx.Exec(ctx, `UPDATE auth_challenges SET consumed_at=now() WHERE id=$1`, id); err != nil {
		return "", "", ErrDependency
	}
	return account, plane, nil
}
func (s *Service) ResendOTP(ctx context.Context, id, requestedPlane string) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	var aid, email, plane, purpose string
	var sent, expiry time.Time
	var tries int
	var used *time.Time
	err = tx.QueryRow(ctx, `SELECT c.account_id,a.email,c.plane,c.purpose,c.last_sent_at,c.expires_at,c.attempts,c.consumed_at FROM auth_challenges c JOIN accounts a ON a.id=c.account_id WHERE c.id=$1 FOR UPDATE OF c`, id).Scan(&aid, &email, &plane, &purpose, &sent, &expiry, &tries, &used)
	if err != nil {
		return ErrInvalid
	}
	if plane != requestedPlane || purpose != "LOGIN_2FA" {
		return ErrForbidden
	}
	if used != nil || !expiry.After(time.Now()) || tries >= s.C.OTPAttempts {
		return ErrExpired
	}
	if time.Since(sent) < s.C.OTPResend {
		return ErrCooldown
	}
	if err = s.limitTx(ctx, tx, "otp_send", aid, time.Hour, s.C.OTPPerHour); err != nil {
		return err
	}
	code, err := security.OTP()
	if err != nil {
		return ErrDependency
	}
	digest := security.Mac(s.C.OTPPepper, "otp", purpose, id, code)
	_, err = tx.Exec(ctx, `UPDATE auth_challenges SET digest=$2,last_sent_at=now(),expires_at=now()+$3::interval WHERE id=$1`, id, digest, fmt.Sprintf("%d seconds", int(s.C.OTPTTL.Seconds())))
	if err != nil {
		return ErrDependency
	}
	if err = s.Mail.Send(ctx, email, "otp", map[string]string{"Code": code, "Action": strings.ReplaceAll(purpose, "_", " "), "Minutes": fmt.Sprint(int(s.C.OTPTTL.Minutes()))}); err != nil {
		return ErrDependency
	}
	return tx.Commit(ctx)
}
func (s *Service) Authenticate(ctx context.Context, bearer, plane string) (Principal, error) {
	if !ValidPlane(plane) || len(bearer) != 43 {
		return Principal{}, ErrInvalid
	}
	var p Principal
	var enabled, verified bool
	var status string
	err := s.Pool.QueryRow(ctx, `SELECT s.account_id,s.id,s.plane,a.email_verified_at IS NOT NULL,a.disabled_at IS NULL,p.status FROM auth_sessions s JOIN accounts a ON a.id=s.account_id JOIN principals p ON p.account_id=s.account_id AND p.plane=s.plane WHERE s.access_digest=$1 AND s.revoked_at IS NULL AND s.access_expires_at>now() AND s.absolute_expires_at>now()`, security.Digest(bearer)).Scan(&p.AccountID, &p.SessionID, &p.Plane, &verified, &enabled, &status)
	if err != nil {
		return Principal{}, ErrInvalid
	}
	if !enabled || !verified || status == "SUSPENDED" {
		return Principal{}, ErrInvalid
	}
	if p.Plane != plane {
		return Principal{}, ErrForbidden
	}
	return p, nil
}
func (s *Service) Profile(ctx context.Context, p Principal) (Profile, error) {
	var v Profile
	err := s.Pool.QueryRow(ctx, `SELECT a.id,a.email,p.plane,p.status,a.email_verified_at IS NOT NULL,a.two_factor_enabled FROM accounts a JOIN principals p ON p.account_id=a.id WHERE a.id=$1 AND p.plane=$2`, p.AccountID, p.Plane).Scan(&v.ID, &v.Email, &v.Plane, &v.PrincipalStatus, &v.EmailVerified, &v.TwoFactorEnabled)
	if err != nil {
		return Profile{}, ErrDependency
	}
	return v, nil
}
func (s *Service) Refresh(ctx context.Context, token, requestedPlane string) (Tokens, error) {
	if len(token) != 43 {
		return Tokens{}, ErrInvalid
	}
	digest := security.Digest(token)
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return Tokens{}, ErrDependency
	}
	defer tx.Rollback(ctx)
	var id, account, plane string
	var refreshExpiry, absoluteExpiry time.Time
	var revoked *time.Time
	var enabled, verified bool
	var status string
	err = tx.QueryRow(ctx, `SELECT s.id,s.account_id,s.plane,s.refresh_expires_at,s.absolute_expires_at,s.revoked_at,a.disabled_at IS NULL,a.email_verified_at IS NOT NULL,p.status FROM auth_sessions s JOIN accounts a ON a.id=s.account_id JOIN principals p ON p.account_id=s.account_id AND p.plane=s.plane WHERE s.refresh_digest=$1 FOR UPDATE OF s`, digest).Scan(&id, &account, &plane, &refreshExpiry, &absoluteExpiry, &revoked, &enabled, &verified, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		// A consumed refresh token can NEVER mint another access token. Reuse revokes its family.
		err = tx.QueryRow(ctx, `SELECT s.id,s.account_id,s.plane FROM consumed_refresh_tokens h JOIN auth_sessions s ON s.id=h.session_id WHERE h.digest=$1 FOR UPDATE OF s`, digest).Scan(&id, &account, &plane)
		if err != nil {
			return Tokens{}, ErrInvalid
		}
		if plane != requestedPlane {
			return Tokens{}, ErrForbidden
		}
		_, err = tx.Exec(ctx, `UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at,now()),revoke_reason='REFRESH_REUSE' WHERE id=$1`, id)
		if err != nil {
			return Tokens{}, ErrDependency
		}
		if err = audit(ctx, tx, account, plane, "REFRESH_REUSE_REVOKED"); err != nil {
			return Tokens{}, ErrDependency
		}
		if err = tx.Commit(ctx); err != nil {
			return Tokens{}, ErrDependency
		}
		return Tokens{}, ErrReused
	}
	if err != nil {
		return Tokens{}, ErrDependency
	}
	if plane != requestedPlane {
		return Tokens{}, ErrForbidden
	}
	if revoked != nil || !enabled || !verified || status == "SUSPENDED" || !refreshExpiry.After(time.Now()) || !absoluteExpiry.After(time.Now()) {
		return Tokens{}, ErrInvalid
	}
	access, ad, err := issueToken()
	if err != nil {
		return Tokens{}, ErrDependency
	}
	refresh, rd, err := issueToken()
	if err != nil {
		return Tokens{}, ErrDependency
	}
	now := time.Now().UTC()
	aexp := now.Add(s.C.AccessTTL)
	rexp := now.Add(s.C.RefreshTTL)
	if rexp.After(absoluteExpiry) {
		rexp = absoluteExpiry
	}
	if _, err = tx.Exec(ctx, `INSERT INTO consumed_refresh_tokens(digest,session_id) VALUES($1,$2)`, digest, id); err != nil {
		return Tokens{}, ErrDependency
	}
	if _, err = tx.Exec(ctx, `UPDATE auth_sessions SET access_digest=$2,refresh_digest=$3,access_expires_at=$4,refresh_expires_at=$5,refresh_generation=refresh_generation+1,last_seen_at=now() WHERE id=$1`, id, ad, rd, aexp, rexp); err != nil {
		return Tokens{}, ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return Tokens{}, ErrDependency
	}
	return Tokens{AccessToken: access, RefreshToken: refresh, TokenType: "Bearer", AccessExpiresAt: aexp, RefreshExpiresAt: rexp, SessionID: id, Plane: plane}, nil
}
func (s *Service) Logout(ctx context.Context, p Principal, all bool) error {
	if all {
		_, err := s.Pool.Exec(ctx, `UPDATE auth_sessions SET revoked_at=now(),revoke_reason='LOGOUT_ALL' WHERE account_id=$1 AND revoked_at IS NULL`, p.AccountID)
		return err
	}
	_, err := s.Pool.Exec(ctx, `UPDATE auth_sessions SET revoked_at=now(),revoke_reason='LOGOUT' WHERE id=$1 AND account_id=$2 AND revoked_at IS NULL`, p.SessionID, p.AccountID)
	return err
}
func (s *Service) ChangePassword(ctx context.Context, p Principal, current, next string) error {
	if err := security.PasswordValid(next); err != nil {
		return err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	var email, oldHash string
	err = tx.QueryRow(ctx, `SELECT email,password_hash FROM accounts WHERE id=$1 FOR UPDATE`, p.AccountID).Scan(&email, &oldHash)
	if err != nil {
		return ErrDependency
	}
	valid, err := security.CheckPassword(current, oldHash, s.C.PasswordPepper)
	if err != nil {
		return ErrDependency
	}
	if !valid {
		return ErrInvalid
	}
	hash, err := security.HashPassword(next, s.C.PasswordPepper, s.C.ArgonMemory, s.C.ArgonIterations, s.C.ArgonParallelism)
	if err != nil {
		return ErrDependency
	}
	if _, err = tx.Exec(ctx, `UPDATE accounts SET password_hash=$2,updated_at=now() WHERE id=$1`, p.AccountID, hash); err != nil {
		return ErrDependency
	}
	if _, err = tx.Exec(ctx, `UPDATE auth_sessions SET revoked_at=now(),revoke_reason='PASSWORD_CHANGED' WHERE account_id=$1 AND revoked_at IS NULL`, p.AccountID); err != nil {
		return ErrDependency
	}
	if err = audit(ctx, tx, p.AccountID, p.Plane, "PASSWORD_CHANGED"); err != nil {
		return ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return ErrDependency
	}
	_ = s.Mail.Send(ctx, email, "security", map[string]string{"Action": "Your password was changed", "When": time.Now().UTC().Format(time.RFC3339)})
	return nil
}
func (s *Service) ForgotPassword(ctx context.Context, email string) error {
	e, err := security.Email(email)
	if err != nil {
		return nil
	}
	if err = s.Limit(ctx, "forgot", e, time.Hour, s.C.OTPPerHour); err != nil {
		return err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	var account string
	err = tx.QueryRow(ctx, `SELECT id FROM accounts WHERE email=$1 AND disabled_at IS NULL AND email_verified_at IS NOT NULL FOR UPDATE`, e).Scan(&account)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return ErrDependency
	}
	token, digest, err := issueToken()
	if err != nil {
		return ErrDependency
	}
	if _, err = tx.Exec(ctx, `DELETE FROM auth_action_tokens WHERE account_id=$1 AND kind='RESET_PASSWORD'`, account); err != nil {
		return ErrDependency
	}
	if _, err = tx.Exec(ctx, `INSERT INTO auth_action_tokens(digest,account_id,kind,expires_at) VALUES($1,$2,'RESET_PASSWORD',now()+interval '20 minutes')`, digest, account); err != nil {
		return ErrDependency
	}
	if err = s.Mail.Send(ctx, e, "reset", map[string]string{"Code": token, "Minutes": "20"}); err != nil {
		return ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return ErrDependency
	}
	return nil
}
func (s *Service) ResetPassword(ctx context.Context, token, next string) error {
	if len(token) != 43 {
		return ErrInvalid
	}
	if err := security.PasswordValid(next); err != nil {
		return err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	var account string
	err = tx.QueryRow(ctx, `UPDATE auth_action_tokens SET consumed_at=now() WHERE digest=$1 AND kind='RESET_PASSWORD' AND consumed_at IS NULL AND expires_at>now() RETURNING account_id`, security.Digest(token)).Scan(&account)
	if err != nil {
		return ErrInvalid
	}
	hash, err := security.HashPassword(next, s.C.PasswordPepper, s.C.ArgonMemory, s.C.ArgonIterations, s.C.ArgonParallelism)
	if err != nil {
		return ErrDependency
	}
	var email string
	err = tx.QueryRow(ctx, `UPDATE accounts SET password_hash=$2,updated_at=now() WHERE id=$1 AND disabled_at IS NULL RETURNING email`, account, hash).Scan(&email)
	if err != nil {
		return ErrInvalid
	}
	if _, err = tx.Exec(ctx, `UPDATE auth_sessions SET revoked_at=now(),revoke_reason='PASSWORD_RESET' WHERE account_id=$1 AND revoked_at IS NULL`, account); err != nil {
		return ErrDependency
	}
	if err = audit(ctx, tx, account, "", "PASSWORD_RESET"); err != nil {
		return ErrDependency
	}
	if err = tx.Commit(ctx); err != nil {
		return ErrDependency
	}
	_ = s.Mail.Send(ctx, email, "security", map[string]string{"Action": "A password reset was completed", "When": time.Now().UTC().Format(time.RFC3339)})
	return nil
}
func (s *Service) Begin2FA(ctx context.Context, p Principal, password string, enable bool) (string, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return "", ErrDependency
	}
	defer tx.Rollback(ctx)
	var hash, email string
	var currently bool
	err = tx.QueryRow(ctx, `SELECT email,password_hash,two_factor_enabled FROM accounts WHERE id=$1 AND disabled_at IS NULL FOR UPDATE`, p.AccountID).Scan(&email, &hash, &currently)
	if err != nil {
		return "", ErrForbidden
	}
	if currently == enable {
		return "", ErrConflict
	}
	match, err := security.CheckPassword(password, hash, s.C.PasswordPepper)
	if err != nil {
		return "", ErrDependency
	}
	if !match {
		return "", ErrInvalid
	}
	purpose := "ENABLE_2FA"
	if !enable {
		purpose = "DISABLE_2FA"
	}
	if err = s.limitTx(ctx, tx, "otp_send", p.AccountID, time.Hour, s.C.OTPPerHour); err != nil {
		return "", err
	}
	id, err := s.makeChallenge(ctx, tx, p.AccountID, email, p.Plane, purpose)
	if err != nil {
		return "", err
	}
	if err = tx.Commit(ctx); err != nil {
		return "", ErrDependency
	}
	return id, nil
}
func (s *Service) Confirm2FA(ctx context.Context, p Principal, challenge, code string, enable bool) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	purpose := "ENABLE_2FA"
	if !enable {
		purpose = "DISABLE_2FA"
	}
	account, plane, err := s.checkChallenge(ctx, tx, challenge, code, purpose)
	if err != nil {
		if errors.Is(err, ErrInvalid) {
			if commitErr := tx.Commit(ctx); commitErr != nil {
				return ErrDependency
			}
		}
		return err
	}
	if account != p.AccountID || plane != p.Plane {
		return ErrForbidden
	}
	tag, err := tx.Exec(ctx, `UPDATE accounts SET two_factor_enabled=$2,updated_at=now() WHERE id=$1 AND two_factor_enabled<>$2 AND disabled_at IS NULL`, account, enable)
	if err != nil {
		return ErrDependency
	}
	if tag.RowsAffected() != 1 {
		return ErrConflict
	}
	// Revoke all other sessions so newly enabled/disabled policy takes immediate effect.
	if _, err = tx.Exec(ctx, `UPDATE auth_sessions SET revoked_at=now(),revoke_reason='2FA_CHANGED' WHERE account_id=$1 AND id<>$2 AND revoked_at IS NULL`, account, p.SessionID); err != nil {
		return ErrDependency
	}
	if err = audit(ctx, tx, account, plane, purpose); err != nil {
		return ErrDependency
	}
	return tx.Commit(ctx)
}
func (s *Service) BootstrapAdmin(ctx context.Context, email, password string) error {
	e, err := security.Email(email)
	if err != nil {
		return ErrInvalid
	}
	if err = security.PasswordValid(password); err != nil {
		return err
	}
	id, err := uuid()
	if err != nil {
		return ErrDependency
	}
	hash, err := security.HashPassword(password, s.C.PasswordPepper, s.C.ArgonMemory, s.C.ArgonIterations, s.C.ArgonParallelism)
	if err != nil {
		return ErrDependency
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)
	var exists bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM accounts WHERE email=$1)`, e).Scan(&exists)
	if err != nil {
		return ErrDependency
	}
	if exists {
		return ErrConflict
	}
	_, err = tx.Exec(ctx, `INSERT INTO accounts(id,email,password_hash,email_verified_at) VALUES($1,$2,$3,now())`, id, e, hash)
	if err != nil {
		return ErrConflict
	}
	if _, err = tx.Exec(ctx, `INSERT INTO principals(account_id,plane,status) VALUES($1,'admin','ACTIVE')`, id); err != nil {
		return ErrDependency
	}
	if err = audit(ctx, tx, id, "admin", "ADMIN_BOOTSTRAPPED_LOCAL_CLI"); err != nil {
		return ErrDependency
	}
	return tx.Commit(ctx)
}
