package config

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo"
	"github.com/joho/godotenv"
)

const (
	defaultHTTPListenAddr        = "127.0.0.1:18080"
	defaultShutdownTimeoutSecond = 30
	minShutdownTimeoutSecond     = 1
	maxShutdownTimeoutSecond     = 300
	defaultTimezone              = "Asia/Kolkata"
)

type AppEnv string

const (
	AppEnvDevelopment AppEnv = "development"
	AppEnvStaging     AppEnv = "staging"
	AppEnvProduction  AppEnv = "production"
)

type LogLevel string

const (
	LogLevelDebug LogLevel = "DEBUG"
	LogLevelInfo  LogLevel = "INFO"
	LogLevelWarn  LogLevel = "WARN"
	LogLevelError LogLevel = "ERROR"
)

type Config struct {
	AppEnv          AppEnv
	HTTPListenAddr  string
	DatabaseURL     string
	LogLevel        LogLevel
	PublicBaseURL   string
	BuildRevision   string
	DefaultTimezone string
	ShutdownTimeout time.Duration
}

// MigrationConfig is the configuration required by the migration executable.
// It deliberately excludes HTTP-only settings.
type MigrationConfig struct {
	AppEnv        AppEnv
	DatabaseURL   string
	BuildRevision string
}

func Load() (Config, error) {
	migrationConfig, err := LoadMigration()
	if err != nil {
		return Config{}, err
	}

	httpListenAddr := envOrDefault(
		"HTTP_LISTEN_ADDR",
		defaultHTTPListenAddr,
	)

	if err := validateListenAddr(httpListenAddr); err != nil {
		return Config{}, fmt.Errorf("HTTP_LISTEN_ADDR: %w", err)
	}

	logLevel, err := logLevelValue(
		envOrDefault("LOG_LEVEL", string(LogLevelInfo)),
	)
	if err != nil {
		return Config{}, err
	}

	publicBaseURL, err := requiredEnv("PUBLIC_BASE_URL")
	if err != nil {
		return Config{}, err
	}

	if err := validatePublicBaseURL(publicBaseURL); err != nil {
		return Config{}, fmt.Errorf("PUBLIC_BASE_URL: %w", err)
	}

	timezone := envOrDefault("DEFAULT_TIMEZONE", defaultTimezone)
	if _, err := time.LoadLocation(timezone); err != nil {
		return Config{}, fmt.Errorf("DEFAULT_TIMEZONE is invalid")
	}

	shutdownTimeout, err := durationSecondsEnv(
		"SHUTDOWN_TIMEOUT_SECONDS",
		defaultShutdownTimeoutSecond,
		minShutdownTimeoutSecond,
		maxShutdownTimeoutSecond,
	)
	if err != nil {
		return Config{}, err
	}

	return Config{
		AppEnv:          migrationConfig.AppEnv,
		HTTPListenAddr:  httpListenAddr,
		DatabaseURL:     migrationConfig.DatabaseURL,
		LogLevel:        logLevel,
		PublicBaseURL:   publicBaseURL,
		BuildRevision:   migrationConfig.BuildRevision,
		DefaultTimezone: timezone,
		ShutdownTimeout: shutdownTimeout,
	}, nil
}

func LoadMigration() (MigrationConfig, error) {
	if err := godotenv.Load(); err != nil &&
		!errors.Is(err, os.ErrNotExist) {
		return MigrationConfig{}, fmt.Errorf("load .env: %w", err)
	}

	appEnv, err := appEnvValue(
		envOrDefault("APP_ENV", string(AppEnvDevelopment)),
	)
	if err != nil {
		return MigrationConfig{}, err
	}

	databaseURL, err := requiredEnv("DATABASE_URL")
	if err != nil {
		return MigrationConfig{}, err
	}

	buildRevision := envOrDefault(
		"BUILD_REVISION",
		"dev",
	)

	if appEnv != AppEnvDevelopment &&
		!buildinfo.IsValidGitSHA(buildRevision) {
		return MigrationConfig{}, fmt.Errorf(
			"BUILD_REVISION must be a 40-character lowercase Git SHA outside development",
		)
	}

	return MigrationConfig{
		AppEnv:        appEnv,
		DatabaseURL:   databaseURL,
		BuildRevision: buildRevision,
	}, nil
}

func appEnvValue(value string) (AppEnv, error) {
	switch AppEnv(strings.ToLower(strings.TrimSpace(value))) {
	case AppEnvDevelopment:
		return AppEnvDevelopment, nil
	case AppEnvStaging:
		return AppEnvStaging, nil
	case AppEnvProduction:
		return AppEnvProduction, nil
	default:
		return "", fmt.Errorf(
			"APP_ENV must be development, staging, or production",
		)
	}
}

func logLevelValue(value string) (LogLevel, error) {
	switch LogLevel(strings.ToUpper(strings.TrimSpace(value))) {
	case LogLevelDebug:
		return LogLevelDebug, nil
	case LogLevelInfo:
		return LogLevelInfo, nil
	case LogLevelWarn:
		return LogLevelWarn, nil
	case LogLevelError:
		return LogLevelError, nil
	default:
		return "", fmt.Errorf(
			"LOG_LEVEL must be DEBUG, INFO, WARN, or ERROR",
		)
	}
}

func envOrDefault(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}

	return fallback
}

func requiredEnv(key string) (string, error) {
	value, ok := os.LookupEnv(key)
	if !ok || strings.TrimSpace(value) == "" {
		return "", fmt.Errorf("%s is required", key)
	}

	if value != strings.TrimSpace(value) {
		return "", fmt.Errorf(
			"%s must not contain leading or trailing whitespace",
			key,
		)
	}

	return value, nil
}

func validateListenAddr(value string) error {
	host, port, err := net.SplitHostPort(value)
	if err != nil {
		return fmt.Errorf("invalid host:port value: %w", err)
	}

	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return fmt.Errorf(
			"host must be a loopback IP address",
		)
	}

	portNumber, err := strconv.Atoi(port)
	if err != nil {
		return fmt.Errorf("port must be numeric")
	}

	if portNumber < 1 || portNumber > 65535 {
		return fmt.Errorf(
			"port must be between 1 and 65535",
		)
	}

	return nil
}

func validatePublicBaseURL(value string) error {
	parsed, err := url.Parse(value)
	if err != nil {
		return fmt.Errorf("invalid URL")
	}

	if parsed.Scheme != "https" {
		return fmt.Errorf("scheme must be https")
	}

	if parsed.Host == "" {
		return fmt.Errorf("host is required")
	}

	if parsed.User != nil {
		return fmt.Errorf("userinfo is not allowed")
	}

	if parsed.RawQuery != "" || parsed.Fragment != "" {
		return fmt.Errorf(
			"query and fragment are not allowed",
		)
	}

	return nil
}

func durationSecondsEnv(
	key string,
	fallback int,
	minimum int,
	maximum int,
) (time.Duration, error) {
	raw := envOrDefault(key, strconv.Itoa(fallback))

	seconds, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf(
			"%s must be an integer number of seconds",
			key,
		)
	}

	if seconds < minimum || seconds > maximum {
		return 0, fmt.Errorf(
			"%s must be between %d and %d seconds",
			key,
			minimum,
			maximum,
		)
	}

	return time.Duration(seconds) * time.Second, nil
}
