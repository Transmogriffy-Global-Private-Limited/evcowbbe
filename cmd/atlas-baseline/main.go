// Command atlas-baseline is a one-time, deterministic bridge from the already
// deployed immutable 001-005 history plus reviewed 006 into Atlas revisions.
// It NEVER opens a database or runs SQL. Run it only from the repository root.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"ariga.io/atlas/sql/migrate"
)

const (
	atlasDir  = "ent/migrate/migrations"
	atlasFile = "20261028000100_foundation_001_006.sql"
)

var immutableHistory = []struct {
	name, checksum string
}{
	{"001_auth.sql", "38b146ed4adb9ef6162843dc2adaede7896d8616cc39395d8b69a09eb85b635c"},
	{"002_outbox_admin_rbac.sql", "3d4fa76de2cd0c8326b9c659c50876ceccab2fb5bbe2542e6a318c75b422adbf"},
	{"003_fleet_inventory.sql", "ea0947bd655fd442069541b8ff0e51b1276c60f64bdbe04b9750e94f798cfc41"},
	{"004_customer_foundation.sql", "4ea9c5776fb94d8b53b80253c84222efe40a14d11420943ae33e3c6a88e75b4a"},
	{"005_admin_pricing_rules.sql", "362de922de83e0661d8117cc8fd65c6c9c17ff1a028cae10f480c079180e729d"},
	{"006_ent_identity_bridge.sql", "093d0bcc31a419cd8400ceeb7c26f86a8000f037246f7a2f88d03861efaaee12"},
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "ATLAS_BASELINE_NOT_GENERATED:", err)
		os.Exit(1)
	}
	fmt.Println("ATLAS_BASELINE=VERIFIED")
}

func run() error {
	if _, err := os.Stat("go.mod"); err != nil {
		return errors.New("run from repository root")
	}
	var body bytes.Buffer
	body.WriteString("-- Immutable SQL foundation snapshot: PostgreSQL 001 through proposed 006.\n")
	body.WriteString("-- New databases replay this baseline; existing databases must use Atlas --baseline\n")
	body.WriteString("-- ONLY after 006 has been applied and independently verified. Do not edit.\n\n")
	for i, item := range immutableHistory {
		original, err := os.ReadFile(filepath.Join("migrations", item.name))
		if err != nil {
			return err
		}
		original, err = canonicalSQLBytes(original)
		if err != nil {
			return fmt.Errorf("canonical source migration %s: %w", item.name, err)
		}
		mirror, err := os.ReadFile(filepath.Join("internal", "database", "migrations", item.name))
		if err != nil {
			return err
		}
		mirror, err = canonicalSQLBytes(mirror)
		if err != nil {
			return fmt.Errorf("canonical embedded migration %s: %w", item.name, err)
		}
		if !bytes.Equal(original, mirror) {
			return fmt.Errorf("legacy migration mirrors differ after line-ending normalization: %s", item.name)
		}
		sum := sha256.Sum256(original)
		if hex.EncodeToString(sum[:]) != item.checksum {
			return fmt.Errorf("immutable migration checksum drift: %s", item.name)
		}
		fmt.Fprintf(&body, "-- Beginning immutable migration %s (%s)\n", item.name, item.checksum)
		body.Write(original)
		if len(original) == 0 || original[len(original)-1] != '\n' {
			body.WriteByte('\n')
		}
		// Migration 005 deliberately checks ledger entry 004, so a clean
		// replay must reproduce the historical ledger *between* scripts.
		fmt.Fprintf(&body, "INSERT INTO schema_migrations(version,checksum) VALUES (%d,'%s');\n", i+1, item.checksum)
		separator := "\n\n"
		if i == len(immutableHistory)-1 {
			separator = "\n"
		}
		fmt.Fprintf(&body, "-- Completed immutable migration %s%s", item.name, separator)
	}
	if err := os.MkdirAll(atlasDir, 0755); err != nil {
		return err
	}
	// Calculate the desired Atlas manifest in memory. We must verify both
	// existing generated files before replacing either: a Windows checkout
	// with CRLF is recoverable; a changed SQL token or checksum is not.
	memory := &migrate.MemDir{}
	if err := memory.WriteFile(atlasFile, body.Bytes()); err != nil {
		return err
	}
	sum, err := memory.Checksum()
	if err != nil {
		return err
	}
	expectedSum, err := sum.MarshalText()
	if err != nil {
		return err
	}
	path := filepath.Join(atlasDir, atlasFile)
	prior, err := os.ReadFile(path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	baselineExists := err == nil
	baselineNeedsLF := false
	if baselineExists {
		canonical, err := canonicalSQLBytes(prior)
		if err != nil || !bytes.Equal(canonical, body.Bytes()) {
			return errors.New("the Atlas baseline differs beyond line endings; refusing overwrite")
		}
		baselineNeedsLF = !bytes.Equal(prior, body.Bytes())
	}
	sumPath := filepath.Join(atlasDir, migrate.HashFileName)
	priorSum, err := os.ReadFile(sumPath)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	sumExists := err == nil
	sumNeedsLF := false
	if sumExists {
		canonical, err := canonicalSQLBytes(priorSum)
		if err != nil || !bytes.Equal(canonical, expectedSum) {
			return errors.New("Atlas checksum manifest differs beyond line endings; review the migration history")
		}
		sumNeedsLF = !bytes.Equal(priorSum, expectedSum)
	}
	// The historical baseline is the only version this one-time command can
	// create or verify. Later Atlas migration files are deliberately managed
	// by the Atlas CLI, not this bootstrap generator.
	localDir, err := migrate.NewLocalDir(atlasDir)
	if err != nil {
		return err
	}
	files, err := localDir.Files()
	if err != nil {
		return err
	}
	if len(files) > 1 || (len(files) == 1 && files[0].Name() != atlasFile) {
		return errors.New("Atlas directory contains post-baseline SQL; bootstrap generator cannot modify history")
	}
	if !baselineExists {
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
		if err != nil {
			return err
		}
		_, writeErr := file.Write(body.Bytes())
		closeErr := file.Close()
		if writeErr != nil {
			return writeErr
		}
		if closeErr != nil {
			return closeErr
		}
	} else if baselineNeedsLF {
		if err := replaceVerifiedGeneratedFile(path, body.Bytes()); err != nil {
			return fmt.Errorf("normalize generated Atlas baseline: %w", err)
		}
		fmt.Println("ATLAS_GENERATED_BASELINE_EOL=CANONICALIZED")
	}
	if !sumExists {
		if err := migrate.WriteSumFile(localDir, sum); err != nil {
			return err
		}
	} else if sumNeedsLF {
		if err := replaceVerifiedGeneratedFile(sumPath, expectedSum); err != nil {
			return fmt.Errorf("normalize generated Atlas checksum manifest: %w", err)
		}
		fmt.Println("ATLAS_GENERATED_SUM_EOL=CANONICALIZED")
	}
	return migrate.Validate(localDir)
}

// canonicalSQLBytes makes the source checkout independent of Git's Windows
// CRLF worktree conversion. Only CRLF -> LF is permitted: other byte changes
// still fail the pinned SHA-256 above. The source files are never rewritten.
func canonicalSQLBytes(source []byte) ([]byte, error) {
	canonical := bytes.ReplaceAll(source, []byte("\r\n"), []byte("\n"))
	if bytes.IndexByte(canonical, '\r') >= 0 {
		return nil, errors.New("bare carriage return in SQL source")
	}
	return canonical, nil
}

// replaceVerifiedGeneratedFile may be used ONLY after canonical content has
// been independently checked against the pinned migration sources. It writes
// a sibling temporary file and renames it, retaining the existing file mode.
// Original migrations 001-006 are never passed to this helper.
func replaceVerifiedGeneratedFile(path string, canonical []byte) (err error) {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("expected an existing regular generated file")
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".atlas-eol-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(info.Mode().Perm()); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(canonical); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}
