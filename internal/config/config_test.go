package config

import (
	"testing"
	"time"
)

const testDatabaseURL = "postgres://test:test@127.0.0.1:5432/testdb?sslmode=disable"

func setRequiredDatabaseURL(t *testing.T) {
	t.Helper()
	t.Setenv("DATABASE_URL", testDatabaseURL)
}

func setHermeticEnvironment(t *testing.T) {
	t.Helper()

	t.Setenv("APP_ENV", "development")
	t.Setenv("HTTP_LISTEN_ADDR", "")
	t.Setenv("DATABASE_URL", "")
	t.Setenv("LOG_LEVEL", "")
	t.Setenv("PUBLIC_BASE_URL", "https://localhost")
	t.Setenv("BUILD_REVISION", "")
	t.Setenv("DEFAULT_TIMEZONE", "")
	t.Setenv("SHUTDOWN_TIMEOUT_SECONDS", "")
}

func TestLoadDefaults(t *testing.T) {
	setHermeticEnvironment(t)
	setRequiredDatabaseURL(t)

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}

	if cfg.HTTPListenAddr != "127.0.0.1:18080" {
		t.Fatalf(
			"expected default HTTP listen address, got %q",
			cfg.HTTPListenAddr,
		)
	}

	if cfg.DatabaseURL != testDatabaseURL {
		t.Fatal("expected DATABASE_URL to be loaded")
	}

	if cfg.ShutdownTimeout != 30*time.Second {
		t.Fatalf(
			"expected 30s shutdown timeout, got %s",
			cfg.ShutdownTimeout,
		)
	}
}

func TestLoadOverrides(t *testing.T) {
	setHermeticEnvironment(t)
	setRequiredDatabaseURL(t)

	t.Setenv("HTTP_LISTEN_ADDR", "127.0.0.1:19090")
	t.Setenv("SHUTDOWN_TIMEOUT_SECONDS", "45")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}

	if cfg.HTTPListenAddr != "127.0.0.1:19090" {
		t.Fatalf(
			"expected overridden HTTP listen address, got %q",
			cfg.HTTPListenAddr,
		)
	}

	if cfg.ShutdownTimeout != 45*time.Second {
		t.Fatalf(
			"expected 45s shutdown timeout, got %s",
			cfg.ShutdownTimeout,
		)
	}
}

func TestLoadRequiresDatabaseURL(t *testing.T) {
	setHermeticEnvironment(t)

	if _, err := Load(); err == nil {
		t.Fatal("expected missing DATABASE_URL to be rejected")
	}
}

func TestLoadRejectsDatabaseURLWhitespace(t *testing.T) {
	setHermeticEnvironment(t)
	t.Setenv(
		"DATABASE_URL",
		" "+testDatabaseURL,
	)

	if _, err := Load(); err == nil {
		t.Fatal("expected DATABASE_URL whitespace to be rejected")
	}
}

func TestLoadRejectsInvalidListenAddr(t *testing.T) {
	setHermeticEnvironment(t)
	setRequiredDatabaseURL(t)

	t.Setenv("HTTP_LISTEN_ADDR", "not-a-listen-address")
	t.Setenv("SHUTDOWN_TIMEOUT_SECONDS", "")

	if _, err := Load(); err == nil {
		t.Fatal("expected invalid listen address to be rejected")
	}
}

func TestLoadRejectsInvalidShutdownTimeout(t *testing.T) {
	tests := []string{
		"not-a-number",
		"0",
		"301",
	}

	for _, value := range tests {
		t.Run(value, func(t *testing.T) {
			setHermeticEnvironment(t)
			setRequiredDatabaseURL(t)
			t.Setenv("SHUTDOWN_TIMEOUT_SECONDS", value)

			if _, err := Load(); err == nil {
				t.Fatalf(
					"expected shutdown timeout %q to be rejected",
					value,
				)
			}
		})
	}
}

func TestLoadRejectsNonLoopbackListenAddr(t *testing.T) {
	setRequiredDatabaseURL(t)

	tests := []string{
		"0.0.0.0:18080",
		"192.168.1.10:18080",
		"localhost:18080",
	}

	for _, value := range tests {
		t.Run(value, func(t *testing.T) {
			setHermeticEnvironment(t)
			setRequiredDatabaseURL(t)
			t.Setenv("HTTP_LISTEN_ADDR", value)

			if _, err := Load(); err == nil {
				t.Fatalf(
					"expected non-loopback listen address %q to be rejected",
					value,
				)
			}
		})
	}
}

func TestLoadAcceptsIPv6LoopbackListenAddr(t *testing.T) {
	setHermeticEnvironment(t)
	setRequiredDatabaseURL(t)

	t.Setenv("HTTP_LISTEN_ADDR", "[::1]:18080")
	t.Setenv("SHUTDOWN_TIMEOUT_SECONDS", "")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}

	if cfg.HTTPListenAddr != "[::1]:18080" {
		t.Fatalf(
			"expected IPv6 loopback address, got %q",
			cfg.HTTPListenAddr,
		)
	}
}

func TestLoadMigrationDoesNotRequireHTTPConfiguration(t *testing.T) {
	setHermeticEnvironment(t)
	setRequiredDatabaseURL(t)
	t.Setenv("PUBLIC_BASE_URL", "")

	cfg, err := LoadMigration()
	if err != nil {
		t.Fatalf("LoadMigration returned error: %v", err)
	}

	if cfg.DatabaseURL != testDatabaseURL {
		t.Fatal("expected DATABASE_URL to be loaded")
	}
}

func TestLoadRejectsInvalidNonDevelopmentBuildRevision(t *testing.T) {
	setHermeticEnvironment(t)
	setRequiredDatabaseURL(t)
	t.Setenv("APP_ENV", "production")
	t.Setenv("BUILD_REVISION", "dev")

	if _, err := Load(); err == nil {
		t.Fatal("expected invalid non-development BUILD_REVISION to be rejected")
	}
}
