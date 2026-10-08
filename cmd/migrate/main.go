package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
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

	if !((len(args) == 1 && (args[0] == "up" || args[0] == "status" || args[0] == "verify")) ||
		(len(args) == 2 && args[0] == "status" && args[1] == "--json")) {
		fmt.Fprintln(stderr, "usage: migrate <up|status|verify> | migrate status --json | migrate verify --files-only")
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
		if len(args) == 2 {
			if err := json.NewEncoder(stdout).Encode(snapshotLedger(status)); err != nil {
				fmt.Fprintln(stderr, "failed to write migration ledger snapshot")
				return 1
			}
			return 0
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

// SnapshotLedger is a read-only, machine-readable actual PostgreSQL ledger
// observation. It is derived from Manager.Status, never from text summaries.
// Exclude credentials, raw SQL, timestamps, and DSNs from this contract.
type ledgerIdentity struct {
	Version                    int64  `json:"version"`
	Name                       string `json:"name"`
	Checksum                   string `json:"checksum"`
	MinCompatibleBinaryVersion *int64 `json:"min_compatible_binary_version"`
}

type ledgerSnapshot struct {
	LedgerPresent bool             `json:"ledger_present"`
	Applied       []ledgerIdentity `json:"applied"`
}

func snapshotLedger(status migrations.Status) ledgerSnapshot {
	result := ledgerSnapshot{LedgerPresent: status.LedgerPresent, Applied: make([]ledgerIdentity, 0)}
	for _, records := range [][]migrations.AppliedMigration{
		status.KnownApplied, status.FutureCompatible, status.FutureIncompatible,
	} {
		for _, record := range records {
			result.Applied = append(result.Applied, ledgerIdentity{
				Version: record.Version, Name: record.Name,
				Checksum:                   record.Checksum,
				MinCompatibleBinaryVersion: record.MinCompatibleBinaryVersion,
			})
		}
	}
	sort.Slice(result.Applied, func(i, j int) bool {
		return result.Applied[i].Version < result.Applied[j].Version
	})
	return result
}
