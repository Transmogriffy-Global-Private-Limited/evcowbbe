package migrate

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"strings"
	"testing"

	atlasmigrate "ariga.io/atlas/sql/migrate"
)

func TestAtlasHistoricalBaselineImmutable(t *testing.T) {
	versions := []struct{ name, digest string }{
		{"001_auth.sql", "38b146ed4adb9ef6162843dc2adaede7896d8616cc39395d8b69a09eb85b635c"},
		{"002_outbox_admin_rbac.sql", "3d4fa76de2cd0c8326b9c659c50876ceccab2fb5bbe2542e6a318c75b422adbf"},
		{"003_fleet_inventory.sql", "ea0947bd655fd442069541b8ff0e51b1276c60f64bdbe04b9750e94f798cfc41"},
		{"004_customer_foundation.sql", "4ea9c5776fb94d8b53b80253c84222efe40a14d11420943ae33e3c6a88e75b4a"},
		{"005_admin_pricing_rules.sql", "362de922de83e0661d8117cc8fd65c6c9c17ff1a028cae10f480c079180e729d"},
		{"006_ent_identity_bridge.sql", "093d0bcc31a419cd8400ceeb7c26f86a8000f037246f7a2f88d03861efaaee12"},
	}
	baselinePath := "migrations/20261028000100_foundation_001_006.sql"
	baseline, err := os.ReadFile(baselinePath)
	if err != nil {
		t.Fatal(err)
	}
	cursor := 0
	for n, v := range versions {
		original, err := os.ReadFile("../../migrations/" + v.name)
		if err != nil {
			t.Fatal(err)
		}
		original, err = canonicalSQLForTest(original)
		if err != nil {
			t.Fatalf("source migration %s: %v", v.name, err)
		}
		mirror, err := os.ReadFile("../../internal/database/migrations/" + v.name)
		if err != nil {
			t.Fatal(err)
		}
		mirror, err = canonicalSQLForTest(mirror)
		if err != nil {
			t.Fatalf("embedded migration %s: %v", v.name, err)
		}
		if !bytes.Equal(original, mirror) {
			t.Fatalf("source migration %s diverges from embedded mirror after LF normalization", v.name)
		}
		sum := sha256.Sum256(original)
		if hex.EncodeToString(sum[:]) != v.digest {
			t.Fatalf("immutable source migration %s checksum changed", v.name)
		}
		begin := []byte("-- Beginning immutable migration " + v.name + " (" + v.digest + ")\n")
		idx := bytes.Index(baseline[cursor:], begin)
		if idx < 0 {
			t.Fatalf("baseline missing migration %s", v.name)
		}
		cursor += idx + len(begin)
		if !bytes.HasPrefix(baseline[cursor:], original) {
			t.Fatalf("baseline migration %s is not a byte-exact replay of canonical LF SQL", v.name)
		}
		cursor += len(original)
		if len(original) == 0 || original[len(original)-1] != '\n' {
			if !bytes.HasPrefix(baseline[cursor:], []byte("\n")) {
				t.Fatalf("baseline missing newline after %s", v.name)
			}
			cursor++
		}
		ledger := fmt.Sprintf("INSERT INTO schema_migrations(version,checksum) VALUES (%d,'%s');", n+1, v.digest)
		if !bytes.HasPrefix(baseline[cursor:], []byte(ledger)) {
			t.Fatalf("baseline %s does not recreate original ledger entry at correct point", v.name)
		}
		cursor += len(ledger)
	}
	if count := strings.Count(string(baseline), "INSERT INTO schema_migrations(version,checksum)"); count != len(versions) {
		t.Fatalf("baseline must record every historical migration once: %d found", count)
	}
	dir, err := atlasmigrate.NewLocalDir("migrations")
	if err != nil {
		t.Fatal(err)
	}
	if err := atlasmigrate.Validate(dir); err != nil {
		t.Fatalf("Atlas history checksum validation: %v", err)
	}
	files, err := dir.Files()
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 1 {
		t.Fatalf("expected one immutable historical baseline, found %d Atlas SQL files", len(files))
	}
}
