package migrate

import (
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"

	"entgo.io/ent/dialect/sql/schema"
)

// Composite principal FKs cannot be generated from Ent's single-column edge
// model. They remain hard database constraints, and their original names are
// preserved by the proposed identity-bridge migration 006.
var compositePrincipalReferences = map[string]string{
	"auth_sessions":              "auth_sessions_account_id_plane_fkey",
	"customer_profiles":          "customer_profiles_account_id_plane_fkey",
	"admin_role_assignments":     "admin_role_assignments_account_id_plane_fkey",
	"admin_capability_overrides": "admin_capability_overrides_account_id_plane_fkey",
}

func TestHistoricalForeignKeysRepresentedInEnt(t *testing.T) {
	tables := make(map[string]*schema.Table)
	for _, table := range Tables {
		tables[table.Name] = table
	}
	// All single-column foreign keys in immutable SQL must appear in the
	// generated model with identical referential delete behavior.
	inline := regexp.MustCompile(`(?im)^\s*([a-z_]+)\s+[a-z0-9_]+\s+(?:NOT\s+NULL\s+)?REFERENCES\s+([a-z_]+)\s*\(\s*([a-z_]+)\s*\)\s+ON\s+DELETE\s+(CASCADE|RESTRICT|SET\s+NULL)`)
	create := regexp.MustCompile(`(?im)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)\s*\(`)
	want := make(map[string]struct{})
	files := []string{"001_auth.sql", "002_outbox_admin_rbac.sql", "003_fleet_inventory.sql", "004_customer_foundation.sql", "005_admin_pricing_rules.sql"}
	for _, file := range files {
		body, err := os.ReadFile("../../migrations/" + file)
		if err != nil {
			t.Fatal(err)
		}
		text := strings.ReplaceAll(string(body), "\r\n", "\n")
		for _, match := range create.FindAllStringSubmatchIndex(text, -1) {
			tableName := text[match[2]:match[3]]
			definition := sqlParentheses(t, text, match[1]-1)
			for _, found := range inline.FindAllStringSubmatch(definition, -1) {
				key := tableName + "." + found[1] + " => " + found[2] + "." + found[3] + " " + strings.ToUpper(strings.Join(strings.Fields(found[4]), " "))
				want[key] = struct{}{}
			}
		}
	}
	got := make(map[string]struct{})
	for _, table := range Tables {
		for _, fk := range table.ForeignKeys {
			if len(fk.Columns) != 1 || len(fk.RefColumns) != 1 || fk.RefTable == nil {
				t.Fatalf("%s: unexpected non-column or unbound Ent foreign key", table.Name)
			}
			key := table.Name + "." + fk.Columns[0].Name + " => " + fk.RefTable.Name + "." + fk.RefColumns[0].Name + " " + string(fk.OnDelete)
			got[key] = struct{}{}
		}
	}
	for key := range want {
		if _, ok := got[key]; !ok {
			t.Errorf("historical single-column FK missing from Ent: %s", key)
		}
	}
	for key := range got {
		if _, ok := want[key]; !ok {
			t.Errorf("Ent introduces an unreviewed single-column FK: %s", key)
		}
	}
	if len(want) != len(got) {
		t.Logf("SQL FK count %d; Ent FK count %d", len(want), len(got))
	}
}

func TestCompositePrincipalReferencesPreservedByBridge(t *testing.T) {
	buf, err := os.ReadFile("../../migrations/006_ent_identity_bridge.sql")
	if err != nil {
		t.Fatal(err)
	}
	text := strings.Join(strings.Fields(string(buf)), " ")
	for table, name := range compositePrincipalReferences {
		quoted := regexp.QuoteMeta("ALTER TABLE " + table + " ADD CONSTRAINT " + name + " FOREIGN KEY (account_id, plane) REFERENCES principals(account_id, plane)")
		if !regexp.MustCompile(quoted).MatchString(text) {
			t.Errorf("migration 006 does not restore composite principal FK %s", name)
		}
	}
	if !strings.Contains(text, "customer_profiles_account_id_plane_fkey FOREIGN KEY (account_id, plane) REFERENCES principals(account_id, plane) ON DELETE RESTRICT") {
		t.Error("customer profiles must retain ON DELETE RESTRICT on the composite principal FK")
	}
	// Guard that this is still exactly the set of composite principal FKs in
	// historical SQL, not a manually curated subset.
	allFiles := []string{"001_auth.sql", "002_outbox_admin_rbac.sql", "004_customer_foundation.sql"}
	composite := regexp.MustCompile(`(?i)FOREIGN\s+KEY\s*\(\s*account_id\s*,\s*plane\s*\)\s+REFERENCES\s+principals\s*\(\s*account_id\s*,\s*plane\s*\)`)
	count := 0
	for _, file := range allFiles {
		body, err := os.ReadFile("../../migrations/" + file)
		if err != nil {
			t.Fatal(err)
		}
		count += len(composite.FindAll(body, -1))
	}
	if count != len(compositePrincipalReferences) {
		t.Errorf("composite principal FK inventory changed: SQL=%d; protected=%d", count, len(compositePrincipalReferences))
	}
}

func sqlParentheses(t *testing.T, source string, open int) string {
	t.Helper()
	if open < 0 || open >= len(source) || source[open] != '(' {
		t.Fatal("expected SQL opening parenthesis")
	}
	depth := 0
	inQuote := false
	for i := open; i < len(source); i++ {
		switch source[i] {
		case '\'':
			if inQuote && i+1 < len(source) && source[i+1] == '\'' {
				i++
			} else {
				inQuote = !inQuote
			}
		case '(':
			if !inQuote {
				depth++
			}
		case ')':
			if !inQuote {
				depth--
				if depth == 0 {
					return source[open+1 : i]
				}
			}
		}
	}
	t.Fatal("unclosed SQL parentheses")
	return ""
}

func TestHistoricalForeignKeysTestInventoryStable(t *testing.T) {
	var tables []string
	for table := range compositePrincipalReferences {
		tables = append(tables, table)
	}
	sort.Strings(tables)
	if strings.Join(tables, ",") != "admin_capability_overrides,admin_role_assignments,auth_sessions,customer_profiles" {
		t.Fatal("composite FK inventory changed; re-review bridge migration")
	}
}

// 006 introduces Ent-compatible IDs but must keep every old composite
// business key, under its historical SQL constraint name, as a unique index.
func TestBridgeNaturalKeysPreserveNamesInEnt(t *testing.T) {
	expected := map[string]string{
		"principals":                    "principals_account_id_plane_key",
		"auth_rate_limits":              "auth_rate_limits_bucket_key_digest_key",
		"admin_role_assignments":        "admin_role_assignments_account_id_role_key_key",
		"admin_capability_overrides":    "admin_capability_overrides_account_id_capability_key",
		"mutation_idempotency_receipts": "mutation_idempotency_receipts_actor_id_request_key_key",
	}
	for _, table := range Tables {
		name, relevant := expected[table.Name]
		if !relevant {
			continue
		}
		found := false
		for _, idx := range table.Indexes {
			if idx.Name == name {
				found = true
				if !idx.Unique {
					t.Errorf("bridge natural key %s must remain UNIQUE", name)
				}
			}
		}
		if !found {
			t.Errorf("Ent generated metadata omitted bridge natural key index %s", name)
		}
		delete(expected, table.Name)
	}
	for table := range expected {
		t.Errorf("missing Ent table with bridge unique index: %s", table)
	}
}
