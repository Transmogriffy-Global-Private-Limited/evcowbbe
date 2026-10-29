package migrate

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// 001-005 are already applied on VPS2 and remain immutable. On Windows, Git
// may have checked out old SQL files with CRLF before .gitattributes was added.
// Canonical LF content must be identical between mirrors; the baseline test
// separately pins every migration's canonical SHA-256. This test rewrites
// neither mirror. The old runner is frozen pending Atlas cutover.
func TestMigrationMirrorsIdentical(t *testing.T) {
	files := []string{
		"001_auth.sql", "002_outbox_admin_rbac.sql", "003_fleet_inventory.sql",
		"004_customer_foundation.sql", "005_admin_pricing_rules.sql",
		"006_ent_identity_bridge.sql",
	}
	for _, name := range files {
		t.Run(name, func(t *testing.T) {
			original, err := os.ReadFile(filepath.Join("..", "..", "migrations", name))
			if err != nil {
				t.Fatal(err)
			}
			mirror, err := os.ReadFile(filepath.Join("..", "..", "internal", "database", "migrations", name))
			if err != nil {
				t.Fatal(err)
			}
			original, err = canonicalSQLForTest(original)
			if err != nil {
				t.Fatal(err)
			}
			mirror, err = canonicalSQLForTest(mirror)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(original, mirror) {
				t.Fatal("embedded and top-level migration content differs after LF normalization")
			}
		})
	}
}

func canonicalSQLForTest(source []byte) ([]byte, error) {
	canonical := bytes.ReplaceAll(source, []byte("\r\n"), []byte("\n"))
	if bytes.IndexByte(canonical, '\r') >= 0 {
		return nil, fmt.Errorf("bare carriage return in SQL migration")
	}
	return canonical, nil
}

func TestIdentityBridgeRetainsNaturalKeys(t *testing.T) {
	raw, err := os.ReadFile("../../migrations/006_ent_identity_bridge.sql")
	if err != nil {
		t.Fatal(err)
	}
	sql := string(raw)
	if regexp.MustCompile(`(?i)\bDROP\s+(TABLE|COLUMN)\b`).MatchString(sql) {
		t.Fatal("identity bridge must never drop tables or columns")
	}
	for _, fragment := range []string{
		"UNIQUE (account_id, plane)",
		"UNIQUE (digest)",
		"UNIQUE (bucket, key_digest)",
		"UNIQUE (account_id, role_key)",
		"UNIQUE (account_id, capability)",
		"UNIQUE (actor_id, request_key)",
		"FOREIGN KEY (account_id, plane) REFERENCES principals(account_id, plane)",
	} {
		if !strings.Contains(sql, fragment) {
			t.Errorf("migration missing critical uniqueness/FK fragment: %s", fragment)
		}
	}
	if got := strings.Count(sql, "ADD COLUMN id bigserial"); got != 7 {
		t.Errorf("expected seven legacy key bridges, got %d", got)
	}
	if got := strings.Count(sql, "DROP CONSTRAINT "); got != 11 {
		t.Errorf("expected four temporarily detached FKs and seven old primary keys, got %d", got)
	}
	if got := strings.Count(sql, "ADD CONSTRAINT "); got != 18 {
		t.Errorf("expected four restored FKs, seven new primary keys and seven natural unique keys, got %d", got)
	}
}
