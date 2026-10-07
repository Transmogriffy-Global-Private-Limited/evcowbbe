package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/migrations"
)

const commandTimeout = 60 * time.Second

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 2 && args[0] == "verify" && args[1] == "--files-only" {
		if err := migrations.VerifyFiles(); err != nil {
			fmt.Fprintf(stderr, "migration file verification failed: %v\n", err)
			return 1
		}
		fmt.Fprintln(stdout, "migration_files=valid")
		return 0
	}

	if len(args) != 1 || (args[0] != "up" && args[0] != "status" && args[0] != "verify") {
		fmt.Fprintln(stderr, "usage: migrate <up|status|verify> | migrate verify --files-only")
		return 2
	}

	cfg, err := config.LoadMigration()
	if err != nil {
		fmt.Fprintf(stderr, "invalid configuration: %v\n", err)
		return 2
	}
	if cfg.AppEnv != config.AppEnvDevelopment {
		if err := buildinfo.ValidateRelease(cfg.BuildRevision); err != nil {
			fmt.Fprintf(stderr, "invalid release identity: %v\n", err)
			return 2
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()

	pool, err := database.Open(ctx, cfg.DatabaseURL)
	if err != nil {
		fmt.Fprintln(stderr, "database unavailable")
		return 1
	}
	defer pool.Close()

	manager := migrations.New(pool)
	switch args[0] {
	case "up":
		if err := manager.Apply(ctx, releaseSHA(cfg.AppEnv)); err != nil {
			fmt.Fprintf(stderr, "migration up failed: %v\n", err)
			return 1
		}
		fmt.Fprintln(stdout, "migration_up=complete")
		return 0

	case "verify":
		if err := manager.Verify(ctx); err != nil {
			fmt.Fprintf(stderr, "migration verification failed: %v\n", err)
			return 1
		}
		fmt.Fprintln(stdout, "migration_ledger=valid")
		return 0

	case "status":
		status, err := manager.Status(ctx)
		if err != nil {
			fmt.Fprintf(stderr, "migration status failed: %v\n", err)
			return 1
		}
		fmt.Fprintf(stdout, "ledger_present=%t known_applied=%d pending=%d future_compatible=%d future_incompatible=%d\n", status.LedgerPresent, len(status.KnownApplied), len(status.Pending), len(status.FutureCompatible), len(status.FutureIncompatible))
		for _, migration := range status.Pending {
			fmt.Fprintf(stdout, "pending=%d_%s\n", migration.Version, migration.Name)
		}
		for _, migration := range status.FutureCompatible {
			fmt.Fprintf(stdout, "future_compatible=%d_%s\n", migration.Version, migration.Name)
		}
		for _, migration := range status.FutureIncompatible {
			fmt.Fprintf(stdout, "future_incompatible=%d_%s\n", migration.Version, migration.Name)
		}
		return 0
	}

	return 1
}

func releaseSHA(appEnv config.AppEnv) string {
	if appEnv == config.AppEnvDevelopment && !buildinfo.IsValidGitSHA(buildinfo.GitSHA) {
		return ""
	}
	return buildinfo.GitSHA
}
