package config

import (
	"bufio"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Env, Address, PublicBaseURL, DatabaseURL                                            string
	DBMaxConns                                                                          int32
	PasswordPepper, OTPPepper                                                           []byte
	AccessTTL, RefreshTTL, AbsoluteTTL, OTPTTL, OTPResend                               time.Duration
	ArgonMemory                                                                         uint32
	ArgonIterations                                                                     uint32
	ArgonParallelism                                                                    uint8
	OTPAttempts, AuthPerMinute, OTPPerHour, RefreshPerMinute                            int
	MailProvider, SMTPHost, SMTPTLSMode, SMTPUser, SMTPPassword, MailFrom, MailFromName string
	SMTPPort                                                                            int
}

// A local .env is used only for development. Process environment always wins.
func Load() (Config, error) {
	if err := readDotEnv(".env"); err != nil {
		return Config{}, err
	}
	c := Config{}
	c.Env = val("APP_ENV", "development")
	if c.Env != "development" && c.Env != "staging" && c.Env != "production" {
		return c, errors.New("APP_ENV must be development, staging, or production")
	}
	c.Address = val("HTTP_LISTEN_ADDR", "127.0.0.1:18080")
	host, _, err := net.SplitHostPort(c.Address)
	if err != nil {
		return c, errors.New("invalid HTTP_LISTEN_ADDR")
	}
	if net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		return c, errors.New("HTTP_LISTEN_ADDR must bind loopback; deploy behind TLS reverse proxy")
	}
	c.PublicBaseURL = val("PUBLIC_BASE_URL", "http://127.0.0.1:18080")
	u, err := url.Parse(c.PublicBaseURL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
		return c, errors.New("invalid PUBLIC_BASE_URL")
	}
	if c.Env != "development" && u.Scheme != "https" {
		return c, errors.New("PUBLIC_BASE_URL must be HTTPS outside development")
	}
	c.DatabaseURL = val("DATABASE_URL", "")
	if c.DatabaseURL == "" {
		return c, errors.New("DATABASE_URL required")
	}
	c.DBMaxConns = int32(number("DB_MAX_CONNS", 10))
	c.PasswordPepper = []byte(val("PASSWORD_PEPPER", ""))
	c.OTPPepper = []byte(val("OTP_HMAC_PEPPER", ""))
	if len(c.PasswordPepper) < 32 || len(c.OTPPepper) < 32 || string(c.PasswordPepper) == string(c.OTPPepper) {
		return c, errors.New("two distinct 32+ character password and OTP peppers required")
	}
	c.AccessTTL = time.Duration(number("ACCESS_TOKEN_TTL_SECONDS", 900)) * time.Second
	c.RefreshTTL = time.Duration(number("REFRESH_TOKEN_TTL_SECONDS", 2592000)) * time.Second
	c.AbsoluteTTL = time.Duration(number("SESSION_ABSOLUTE_TTL_SECONDS", 7776000)) * time.Second
	c.OTPTTL = time.Duration(number("OTP_TTL_SECONDS", 600)) * time.Second
	c.OTPResend = time.Duration(number("OTP_RESEND_COOLDOWN_SECONDS", 60)) * time.Second
	c.ArgonMemory = uint32(number("PASSWORD_ARGON2_MEMORY_KIB", 65536))
	c.ArgonIterations = uint32(number("PASSWORD_ARGON2_ITERATIONS", 3))
	c.ArgonParallelism = uint8(number("PASSWORD_ARGON2_PARALLELISM", 1))
	c.OTPAttempts = number("OTP_MAX_ATTEMPTS", 5)
	c.AuthPerMinute = number("RATE_AUTH_PER_MINUTE", 10)
	c.OTPPerHour = number("RATE_OTP_SEND_PER_HOUR", 6)
	c.RefreshPerMinute = number("RATE_REFRESH_PER_MINUTE", 12)
	if c.DBMaxConns < 1 || c.DBMaxConns > 50 || c.AccessTTL < time.Minute || c.AccessTTL > time.Hour || c.RefreshTTL < time.Hour || c.RefreshTTL > c.AbsoluteTTL || c.AbsoluteTTL > 90*24*time.Hour || c.OTPTTL < time.Minute || c.OTPTTL > 15*time.Minute || c.OTPAttempts < 1 || c.OTPAttempts > 10 || c.OTPResend < 30*time.Second || c.OTPResend > 10*time.Minute || c.AuthPerMinute < 1 || c.AuthPerMinute > 200 || c.OTPPerHour < 1 || c.OTPPerHour > 30 || c.RefreshPerMinute < 1 || c.RefreshPerMinute > 200 || c.ArgonMemory < 65536 || c.ArgonMemory > 262144 || c.ArgonIterations < 3 || c.ArgonIterations > 10 || c.ArgonParallelism < 1 || c.ArgonParallelism > 4 {
		return c, errors.New("authentication configuration outside safe limits")
	}
	c.MailProvider = val("MAIL_PROVIDER", "SMTP")
	c.SMTPHost = val("SMTP_HOST", "127.0.0.1")
	c.SMTPPort = number("SMTP_PORT", 1025)
	c.SMTPTLSMode = val("SMTP_TLS_MODE", "none")
	c.SMTPUser = val("SMTP_USERNAME", "")
	c.SMTPPassword = val("SMTP_PASSWORD", "")
	c.MailFrom = val("MAIL_FROM_ADDRESS", "")
	c.MailFromName = val("MAIL_FROM_NAME", "Charger on Wheels")
	if c.MailProvider != "SMTP" || c.SMTPPort < 1 || c.SMTPPort > 65535 || c.MailFrom == "" {
		return c, errors.New("valid SMTP mailer configuration required")
	}
	if c.SMTPTLSMode != "none" && c.SMTPTLSMode != "starttls" && c.SMTPTLSMode != "tls" {
		return c, errors.New("SMTP_TLS_MODE must be none, starttls or tls")
	}
	if c.SMTPTLSMode == "none" && (c.Env != "development" || (c.SMTPHost != "127.0.0.1" && c.SMTPHost != "localhost" && c.SMTPHost != "::1")) {
		return c, errors.New("unencrypted SMTP only permitted for loopback development")
	}
	if (c.SMTPUser == "") != (c.SMTPPassword == "") {
		return c, errors.New("SMTP_USERNAME and SMTP_PASSWORD must both be set or both empty")
	}
	return c, nil
}
func val(k, d string) string {
	s, ok := os.LookupEnv(k)
	if !ok {
		return d
	}
	return s
}
func number(k string, d int) int {
	s := val(k, "")
	if s == "" {
		return d
	}
	n, e := strconv.Atoi(s)
	if e != nil {
		return -1
	}
	return n
}
func readDotEnv(filename string) error {
	f, err := os.Open(filename)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("open env: %w", err)
	}
	defer f.Close()
	scan := bufio.NewScanner(f)
	for scan.Scan() {
		line := strings.TrimSpace(scan.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		pair := strings.SplitN(line, "=", 2)
		if len(pair) != 2 {
			return errors.New("malformed .env line")
		}
		key := strings.TrimSpace(pair[0])
		if key == "" || strings.ContainsAny(key, " \t\r\n") {
			return errors.New("invalid .env key")
		}
		if _, exists := os.LookupEnv(key); exists {
			continue
		}
		value := strings.TrimSpace(pair[1])
		if len(value) >= 2 && ((value[0] == '"' && value[len(value)-1] == '"') || (value[0] == '\'' && value[len(value)-1] == '\'')) {
			value = value[1 : len(value)-1]
		}
		os.Setenv(key, value)
	}
	if err := scan.Err(); err != nil {
		return errors.New("error reading .env")
	}
	return nil
}
