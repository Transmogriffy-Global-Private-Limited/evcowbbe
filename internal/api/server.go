package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/adminrbac"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/user"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Server struct {
	Auth   *auth.Service
	DB     *pgxpool.Pool
	C      config.Config
	Logger *slog.Logger
	User   *user.Router
}
type response struct {
	Data any `json:"data"`
}
type failure struct {
	Error struct {
		Code      string         `json:"code"`
		Message   string         `json:"message"`
		RequestID string         `json:"request_id"`
		Details   map[string]any `json:"details"`
	} `json:"error"`
}
type creds struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}
type onlyToken struct {
	Token string `json:"token"`
}
type otpReq struct {
	ChallengeID string `json:"challenge_id"`
	Code        string `json:"code"`
}
type challengeReq struct {
	ChallengeID string `json:"challenge_id"`
}
type refreshReq struct {
	RefreshToken string `json:"refresh_token"`
}
type changeReq struct {
	CurrentPassword string `json:"current_password"`
	NewPassword     string `json:"new_password"`
}
type resetReq struct {
	Token       string `json:"token"`
	NewPassword string `json:"new_password"`
}
type begin2fa struct {
	Password string `json:"password"`
}

func (s *Server) Handler() http.Handler { return http.HandlerFunc(s.serve) }
func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	id, _ := security.UUID()
	if incoming := r.Header.Get("X-Request-ID"); len(incoming) == 36 && uuidFormat(incoming) {
		id = incoming
	}
	w.Header().Set("X-Request-ID", id)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("X-Frame-Options", "DENY")
	if s.C.Env != "development" {
		w.Header().Set("Strict-Transport-Security", "max-age=31536000")
	}
	if s.cors(w, r, id) {
		return
	}
	if r.Method == http.MethodOptions {
		s.fail(w, id, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
		return
	}
	if isDocumentationPath(r.URL.Path) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			s.fail(w, id, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed")
			return
		}
		serveDocumentation(w, r)
		return
	}
	if r.URL.Path == "/health/live" && r.Method == "GET" {
		s.ok(w, http.StatusOK, map[string]any{"status": "live"})
		return
	}
	if r.URL.Path == "/health/ready" && r.Method == "GET" {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		if err := s.DB.Ping(ctx); err != nil {
			s.fail(w, id, 503, "DEPENDENCY_UNAVAILABLE", "Database unavailable")
			return
		}
		s.ok(w, 200, map[string]any{"status": "ready"})
		return
	}
	if r.URL.Path == "/version" && r.Method == "GET" {
		s.ok(w, 200, map[string]string{"version": "fleet-inventory-1"})
		return
	}
	if !strings.HasPrefix(r.URL.Path, "/api/v1/") {
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 12*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/")
	// Public global flows, identity not exposed by response.
	if strings.HasPrefix(path, "auth/") {
		switch path {
		case "auth/verify-email":
			if !post(w, r, id, s) {
				return
			}
			var v onlyToken
			if !s.decode(w, r, id, &v) {
				return
			}
			s.done(w, id, s.Auth.VerifyEmail(ctx, v.Token))
			return
		case "auth/resend-verification":
			if !post(w, r, id, s) {
				return
			}
			var v struct {
				Email string `json:"email"`
			}
			if !s.decode(w, r, id, &v) {
				return
			}
			err := s.Auth.Limit(ctx, "verify_ip", remoteIP(r), time.Minute, s.C.AuthPerMinute)
			if err == nil {
				err = s.Auth.ResendVerification(ctx, v.Email)
			}
			s.doneGeneric(w, id, err)
			return
		case "auth/forgot-password":
			if !post(w, r, id, s) {
				return
			}
			var v struct {
				Email string `json:"email"`
			}
			if !s.decode(w, r, id, &v) {
				return
			}
			err := s.Auth.Limit(ctx, "forgot_ip", remoteIP(r), time.Minute, s.C.AuthPerMinute)
			if err == nil {
				err = s.Auth.ForgotPassword(ctx, v.Email)
			}
			s.doneGeneric(w, id, err)
			return
		case "auth/reset-password":
			if !post(w, r, id, s) {
				return
			}
			var v resetReq
			if !s.decode(w, r, id, &v) {
				return
			}
			err := s.Auth.Limit(ctx, "reset_ip", remoteIP(r), time.Minute, s.C.AuthPerMinute)
			if err == nil {
				err = s.Auth.ResetPassword(ctx, v.Token, v.NewPassword)
			}
			s.done(w, id, err)
			return
		}
	}
	bits := strings.Split(path, "/")
	if len(bits) < 2 || !auth.ValidPlane(bits[0]) {
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	plane := bits[0]
	action := strings.Join(bits[1:], "/")
	if action == "auth/register" {
		if plane == "admin" {
			s.fail(w, id, 403, "FORBIDDEN", "Admin registration is disabled")
			return
		}
		if !post(w, r, id, s) {
			return
		}
		var v creds
		if !s.decode(w, r, id, &v) {
			return
		}
		e, err := security.Email(v.Email)
		if err != nil {
			s.err(w, id, auth.ErrInvalid)
			return
		}
		if err = s.Auth.Limit(ctx, "register_ip", remoteIP(r), time.Minute, s.C.AuthPerMinute); err == nil {
			err = s.Auth.Limit(ctx, "register_email", e, time.Hour, s.C.OTPPerHour)
		}
		if err == nil {
			err = s.Auth.Register(ctx, plane, v.Email, v.Password)
		}
		if err != nil && !errors.Is(err, auth.ErrConflict) {
			s.err(w, id, err)
			return
		}
		s.ok(w, 202, map[string]any{"message": "If eligible, a verification email has been queued", "plane": plane})
		return
	}
	if action == "auth/login" {
		if !post(w, r, id, s) {
			return
		}
		var v creds
		if !s.decode(w, r, id, &v) {
			return
		}
		e, err := security.Email(v.Email)
		if err != nil {
			s.err(w, id, auth.ErrInvalid)
			return
		}
		if err = s.Auth.Limit(ctx, "login_ip", remoteIP(r), time.Minute, s.C.AuthPerMinute); err == nil {
			err = s.Auth.Limit(ctx, "login_email", e, time.Minute, s.C.AuthPerMinute)
		}
		if err != nil {
			s.err(w, id, err)
			return
		}
		o, err := s.Auth.Login(ctx, plane, v.Email, v.Password)
		if err != nil {
			s.err(w, id, err)
			return
		}
		s.ok(w, 200, o)
		return
	}
	if action == "auth/verify-otp" {
		if !post(w, r, id, s) {
			return
		}
		var v otpReq
		if !s.decode(w, r, id, &v) {
			return
		}
		err := s.Auth.Limit(ctx, "otp_verify_ip", remoteIP(r), time.Minute, s.C.AuthPerMinute)
		if err != nil {
			s.err(w, id, err)
			return
		}
		token, err := s.Auth.VerifyLoginOTP(ctx, v.ChallengeID, v.Code, plane)
		if err != nil {
			s.err(w, id, err)
			return
		}
		s.ok(w, 200, token)
		return
	}
	if action == "auth/refresh" {
		if !post(w, r, id, s) {
			return
		}
		var v refreshReq
		if !s.decode(w, r, id, &v) {
			return
		}
		err := s.Auth.Limit(ctx, "refresh_ip", remoteIP(r), time.Minute, s.C.RefreshPerMinute)
		if err != nil {
			s.err(w, id, err)
			return
		}
		token, err := s.Auth.Refresh(ctx, v.RefreshToken, plane)
		if err != nil {
			s.err(w, id, err)
			return
		}
		s.ok(w, 200, token)
		return
	}
	if action == "auth/2fa/resend" {
		if !post(w, r, id, s) {
			return
		}
		var v challengeReq
		if !s.decode(w, r, id, &v) {
			return
		}
		err := s.Auth.Limit(ctx, "resend_ip", remoteIP(r), time.Minute, s.C.AuthPerMinute)
		if err == nil {
			err = s.Auth.ResendOTP(ctx, v.ChallengeID, plane)
		}
		s.done(w, id, err)
		return
	}
	// All other plane-specific actions require an access bearer token.
	var principal auth.Principal
	bearer := r.Header.Get("Authorization")
	if strings.HasPrefix(bearer, "Bearer ") && strings.Count(bearer, " ") == 1 {
		var err error
		principal, err = s.Auth.Authenticate(ctx, strings.TrimPrefix(bearer, "Bearer "), plane)
		if err != nil {
			s.err(w, id, err)
			return
		}
	} else {
		s.err(w, id, auth.ErrInvalid)
		return
	}
	if plane == "admin" && strings.HasPrefix(action, "rbac/") {
		s.handleRBAC(w, r, id, principal, strings.TrimPrefix(action, "rbac/"))
		return
	}
	if plane == "admin" && isFleetRoute(action) {
		s.handleFleet(w, r, id, principal, action)
		return
	}
	switch action {
	case "me":
		if r.Method != "GET" {
			s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
			return
		}
		profile, err := s.Auth.Profile(ctx, principal)
		if err != nil {
			s.err(w, id, err)
			return
		}
		s.ok(w, 200, profile)
	case "auth/logout":
		if !post(w, r, id, s) {
			return
		}
		s.done(w, id, s.Auth.Logout(ctx, principal, false))
	case "auth/logout-all":
		if !post(w, r, id, s) {
			return
		}
		s.done(w, id, s.Auth.Logout(ctx, principal, true))
	case "auth/password/change":
		if !post(w, r, id, s) {
			return
		}
		var v changeReq
		if !s.decode(w, r, id, &v) {
			return
		}
		err := s.Auth.Limit(ctx, "pwd_change", principal.AccountID, time.Minute, 5)
		if err == nil {
			err = s.Auth.ChangePassword(ctx, principal, v.CurrentPassword, v.NewPassword)
		}
		s.done(w, id, err)
	case "auth/2fa/enable/begin", "auth/2fa/disable/begin":
		if !post(w, r, id, s) {
			return
		}
		var v begin2fa
		if !s.decode(w, r, id, &v) {
			return
		}
		enabled := action == "auth/2fa/enable/begin"
		err := s.Auth.Limit(ctx, "2fa_begin", principal.AccountID, time.Minute, 5)
		if err != nil {
			s.err(w, id, err)
			return
		}
		challenge, err := s.Auth.Begin2FA(ctx, principal, v.Password, enabled)
		if err != nil {
			s.err(w, id, err)
			return
		}
		s.ok(w, 200, map[string]string{"challenge_id": challenge, "message": "Check email for one-time code"})
	case "auth/2fa/enable/confirm", "auth/2fa/disable/confirm":
		if !post(w, r, id, s) {
			return
		}
		var v otpReq
		if !s.decode(w, r, id, &v) {
			return
		}
		enabled := action == "auth/2fa/enable/confirm"
		err := s.Auth.Limit(ctx, "2fa_confirm", principal.AccountID, time.Minute, s.C.AuthPerMinute)
		if err == nil {
			err = s.Auth.Confirm2FA(ctx, principal, v.ChallengeID, v.Code, enabled)
		}
		s.done(w, id, err)
	default:
		if action == "vehicles" || strings.HasPrefix(action, "vehicles/") {
			if plane != "app" {
				s.fail(w, id, 403, "FORBIDDEN", "Vehicle management is only available on the customer app plane")
				return
			}
			s.getUserRouter().DispatchWithPrincipal(w, r, principal, id)
			return
		}
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
	}
}

type rbacReason struct {
	Reason string `json:"reason"`
}
type rbacRole struct {
	Role   string `json:"role"`
	Reason string `json:"reason"`
}
type rbacOverride struct {
	Decision string `json:"decision"`
	Reason   string `json:"reason"`
}

func (s *Server) rbacError(w http.ResponseWriter, id string, err error) {
	switch {
	case errors.Is(err, adminrbac.ErrForbidden):
		s.fail(w, id, 403, "CAPABILITY_DENIED", "Administrative capability required")
	case errors.Is(err, adminrbac.ErrInvalid):
		s.fail(w, id, 422, "VALIDATION_FAILED", "Invalid RBAC request")
	case errors.Is(err, adminrbac.ErrNotFound):
		s.fail(w, id, 404, "NOT_FOUND", "Admin not found")
	case errors.Is(err, adminrbac.ErrConflict):
		s.fail(w, id, 409, "CONFLICT", "RBAC change would violate a safety rule")
	default:
		s.fail(w, id, 503, "DEPENDENCY_UNAVAILABLE", "RBAC data unavailable")
	}
}

func (s *Server) handleRBAC(w http.ResponseWriter, r *http.Request, id string, principal auth.Principal, path string) {
	rbac := adminrbac.Service{Pool: s.DB}
	if path == "me" && r.Method == http.MethodGet {
		v, err := rbac.Snapshot(r.Context(), principal.AccountID)
		if err != nil {
			s.rbacError(w, id, err)
			return
		}
		s.ok(w, 200, v)
		return
	}
	if path == "catalog" && r.Method == http.MethodGet {
		if err := rbac.Require(r.Context(), principal.AccountID, "system.settings.read"); err != nil {
			s.rbacError(w, id, err)
			return
		}
		s.ok(w, 200, map[string]any{"capabilities": adminrbac.Capabilities(), "roles": adminrbac.Catalog()})
		return
	}
	parts := strings.Split(path, "/")
	if len(parts) < 2 || parts[0] != "accounts" || !uuidFormat(parts[1]) {
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	target := parts[1]
	if len(parts) == 2 && r.Method == http.MethodGet {
		if err := rbac.Require(r.Context(), principal.AccountID, "system.settings.read"); err != nil {
			s.rbacError(w, id, err)
			return
		}
		var exists bool
		if err := s.DB.QueryRow(r.Context(), `SELECT EXISTS(SELECT 1 FROM principals WHERE account_id=$1 AND plane='admin')`, target).Scan(&exists); err != nil {
			s.rbacError(w, id, err)
			return
		}
		if !exists {
			s.rbacError(w, id, adminrbac.ErrNotFound)
			return
		}
		v, err := rbac.Snapshot(r.Context(), target)
		if err != nil {
			s.rbacError(w, id, err)
			return
		}
		s.ok(w, 200, v)
		return
	}
	var err error
	switch {
	case len(parts) == 3 && parts[2] == "roles" && r.Method == http.MethodPost:
		if !post(w, r, id, s) {
			return
		}
		var v rbacRole
		if !s.decode(w, r, id, &v) {
			return
		}
		err = rbac.AddRole(r.Context(), principal.AccountID, target, v.Role, v.Reason)
	case len(parts) == 4 && parts[2] == "roles" && r.Method == http.MethodDelete:
		if !s.mutationBody(w, r, id, http.MethodDelete) {
			return
		}
		var v rbacReason
		if !s.decode(w, r, id, &v) {
			return
		}
		err = rbac.RemoveRole(r.Context(), principal.AccountID, target, parts[3], v.Reason)
	case len(parts) == 4 && parts[2] == "overrides" && r.Method == http.MethodPut:
		if !s.mutationBody(w, r, id, http.MethodPut) {
			return
		}
		var v rbacOverride
		if !s.decode(w, r, id, &v) {
			return
		}
		err = rbac.SetOverride(r.Context(), principal.AccountID, target, parts[3], v.Decision, v.Reason)
	case len(parts) == 4 && parts[2] == "overrides" && r.Method == http.MethodDelete:
		if !s.mutationBody(w, r, id, http.MethodDelete) {
			return
		}
		var v rbacReason
		if !s.decode(w, r, id, &v) {
			return
		}
		err = rbac.RemoveOverride(r.Context(), principal.AccountID, target, parts[3], v.Reason)
	default:
		s.fail(w, id, 404, "NOT_FOUND", "Endpoint not found")
		return
	}
	if err != nil {
		s.rbacError(w, id, err)
		return
	}
	s.ok(w, 200, map[string]bool{"ok": true})
}

func (s *Server) mutationBody(w http.ResponseWriter, r *http.Request, id, method string) bool {
	if r.Method != method {
		s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		return false
	}
	if strings.ToLower(strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0])) != "application/json" {
		s.fail(w, id, 415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")
		return false
	}
	return true
}

func (s *Server) getUserRouter() *user.Router {
	if s.User != nil {
		return s.User
	}
	repo := user.NewRepository(s.DB)
	svc := user.NewService(repo)
	s.User = user.NewRouter(svc, s.Auth)
	return s.User
}
func uuidFormat(s string) bool {
	if len(s) != 36 {
		return false
	}
	for i, c := range s {
		if i == 8 || i == 13 || i == 18 || i == 23 {
			if c != '-' {
				return false
			}
			continue
		}
		if !strings.ContainsRune("0123456789abcdef", c) {
			return false
		}
	}
	return true
}
func remoteIP(r *http.Request) string {
	ip, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	// The public server only listens on loopback, behind the EVCOWBBE Caddy
	// route. Caddy overwrites X-Real-IP with the Cloudflare-asserted visitor
	// address, but only when the immediate TCP peer belongs to Cloudflare's
	// published ranges. Never trust this header from a non-loopback peer.
	if peer := net.ParseIP(ip); peer != nil && peer.IsLoopback() {
		values := r.Header.Values("X-Real-IP")
		if len(values) == 1 {
			value := strings.TrimSpace(values[0])
			if real := net.ParseIP(value); real != nil && !real.IsUnspecified() && !real.IsLoopback() {
				return real.String()
			}
		}
	}
	return ip
}
func post(w http.ResponseWriter, r *http.Request, id string, s *Server) bool {
	if r.Method != "POST" {
		s.fail(w, id, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
		return false
	}
	if strings.ToLower(strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0])) != "application/json" {
		s.fail(w, id, 415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")
		return false
	}
	return true
}
func (s *Server) decode(w http.ResponseWriter, r *http.Request, id string, out any) bool {
	reader := http.MaxBytesReader(w, r.Body, 32768)
	defer reader.Close()
	d := json.NewDecoder(reader)
	d.DisallowUnknownFields()
	if err := d.Decode(out); err != nil {
		s.fail(w, id, 400, "VALIDATION_FAILED", "Invalid JSON request")
		return false
	}
	var rest any
	if err := d.Decode(&rest); err != io.EOF {
		s.fail(w, id, 400, "VALIDATION_FAILED", "Only one JSON object permitted")
		return false
	}
	return true
}
func (s *Server) ok(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(response{Data: data})
}
func (s *Server) fail(w http.ResponseWriter, id string, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	var o failure
	o.Error.Code = code
	o.Error.Message = message
	o.Error.RequestID = id
	o.Error.Details = map[string]any{}
	_ = json.NewEncoder(w).Encode(o)
}
func (s *Server) err(w http.ResponseWriter, id string, err error) {
	if err == nil {
		s.ok(w, 200, map[string]bool{"ok": true})
		return
	}
	switch {
	case errors.Is(err, auth.ErrInvalid):
		s.fail(w, id, 401, "INVALID_CREDENTIALS", "Invalid credentials or token")
	case errors.Is(err, auth.ErrUnverified):
		s.fail(w, id, 403, "EMAIL_NOT_VERIFIED", "Verify your email first")
	case errors.Is(err, auth.ErrForbidden):
		s.fail(w, id, 403, "PRINCIPAL_PLANE_MISMATCH", "Access not permitted for this principal")
	case errors.Is(err, auth.ErrConflict):
		s.fail(w, id, 409, "CONFLICT", "Account or state already exists")
	case errors.Is(err, auth.ErrRate), errors.Is(err, auth.ErrCooldown):
		w.Header().Set("Retry-After", strconv.Itoa(60))
		s.fail(w, id, 429, "RATE_LIMITED", "Please try again later")
	case errors.Is(err, auth.ErrExpired):
		s.fail(w, id, 401, "OTP_EXPIRED", "Code expired or exhausted")
	case errors.Is(err, auth.ErrReused):
		s.fail(w, id, 401, "REFRESH_TOKEN_REUSED", "Session revoked; sign in again")
	case errors.Is(err, auth.ErrDependency):
		s.fail(w, id, 503, "DEPENDENCY_UNAVAILABLE", "Required service unavailable")
	default:
		// Distinguish public password policy violations; never include database/SMTP details.
		if strings.HasPrefix(err.Error(), "password ") {
			s.fail(w, id, 422, "VALIDATION_FAILED", err.Error())
			return
		}
		if s.Logger != nil {
			s.Logger.Error("auth request failed", "type", "internal")
		}
		s.fail(w, id, 500, "INTERNAL_ERROR", "Internal error")
	}
}
func (s *Server) done(w http.ResponseWriter, id string, err error) {
	if err != nil {
		s.err(w, id, err)
		return
	}
	s.ok(w, 200, map[string]bool{"ok": true})
}
func (s *Server) doneGeneric(w http.ResponseWriter, id string, err error) {
	if errors.Is(err, auth.ErrDependency) {
		s.err(w, id, err)
		return
	}
	if err != nil && !errors.Is(err, auth.ErrInvalid) && !errors.Is(err, auth.ErrConflict) {
		s.err(w, id, err)
		return
	}
	s.ok(w, 200, map[string]bool{"accepted": true})
}
