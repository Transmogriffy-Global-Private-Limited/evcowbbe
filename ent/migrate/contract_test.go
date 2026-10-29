package migrate

import (
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"

	"entgo.io/ent/schema/field"
)

// TestModelSQLColumnParity guards the complete historical CMS table inventory
// during the Ent/Atlas transition. The operational migration history remains
// unchanged; SQL-only CHECK/FK/partial-index invariants require separate tests.
func TestModelSQLColumnParity(t *testing.T) {
	for _, lineEnding := range []string{"LF", "CRLF"} {
		t.Run(lineEnding, func(t *testing.T) {
			checkModelSQLColumnParity(t, lineEnding == "CRLF")
		})
	}
}

func checkModelSQLColumnParity(t *testing.T, asCRLF bool) {
	t.Helper()
	tables := map[string]map[string]string{}
	for _, filename := range []string{
		"001_auth.sql", "002_outbox_admin_rbac.sql", "003_fleet_inventory.sql",
		"004_customer_foundation.sql", "005_admin_pricing_rules.sql",
	} {
		raw, err := os.ReadFile("../../migrations/" + filename)
		if err != nil {
			t.Fatal(err)
		}
		ddl := strings.ReplaceAll(string(raw), "\r\n", "\n")
		if asCRLF {
			ddl = strings.ReplaceAll(ddl, "\n", "\r\n")
		}
		for name, cols := range parseDDLColumns(ddl) {
			tables[name] = cols
		}
	}
	// Migration bookkeeping is intentionally not an Ent business model.
	delete(tables, "schema_migrations")
	// Six? No: seven legacy natural/bytea primary keys acquire an Ent-friendly
	// surrogate ID in the explicit additive migration 006. Never assume the
	// historical 001-005 tables already contain these columns.
	bridge, err := os.ReadFile("../../migrations/006_ent_identity_bridge.sql")
	if err != nil {
		t.Fatal(err)
	}
	addedID := regexp.MustCompile(`(?m)^ALTER TABLE ([a-z_]+) ADD COLUMN id bigserial;$`)
	additions := addedID.FindAllStringSubmatch(string(bridge), -1)
	if len(additions) != 7 {
		t.Fatalf("expected 7 explicit surrogate IDs, got %d", len(additions))
	}
	for _, match := range additions {
		cols, ok := tables[match[1]]
		if !ok {
			t.Fatalf("006 adds ID to unknown table %q", match[1])
		}
		if _, exists := cols["id"]; exists {
			t.Fatalf("006 adds duplicate ID to %q", match[1])
		}
		cols["id"] = "bigserial"
	}
	if len(tables) != len(Tables) {
		t.Fatalf("migration has %d tables; generated model has %d", len(tables), len(Tables))
	}
	for _, model := range Tables {
		actual, ok := tables[model.Name]
		if !ok {
			t.Errorf("Ent model %s not found in reviewed SQL", model.Name)
			continue
		}
		want := make(map[string]string)
		for _, c := range model.Columns {
			var typ string
			switch c.Type {
			case field.TypeUUID:
				typ = "uuid"
			case field.TypeString, field.TypeEnum:
				typ = "text"
			case field.TypeInt32:
				typ = "integer"
			case field.TypeInt64:
				typ = "bigint"
				if c.Increment {
					typ = "bigserial"
				}
			case field.TypeBool:
				typ = "boolean"
			case field.TypeBytes:
				typ = "bytea"
			case field.TypeTime:
				typ = "timestamptz"
				if c.SchemaType["postgres"] == "date" {
					typ = "date"
				}
			case field.TypeOther:
				typ = c.SchemaType["postgres"]
			case field.TypeJSON:
				typ = "jsonb"
			default:
				t.Errorf("%s.%s: unsupported Ent type %v", model.Name, c.Name, c.Type)
			}
			want[c.Name] = typ
		}
		if len(want) != len(actual) {
			t.Errorf("%s: SQL has %d columns, Ent has %d", model.Name, len(actual), len(want))
		}
		for name, typ := range want {
			if actual[name] != typ {
				t.Errorf("%s.%s: Ent=%s, migration=%s", model.Name, name, typ, actual[name])
			}
		}
		for name := range actual {
			if _, ok := want[name]; !ok {
				t.Errorf("%s.%s: SQL column lacks an Ent model", model.Name, name)
			}
		}
	}

	// This intentionally covers the Ent-modelled slice, not all prior tables.
	var names []string
	for n := range tables {
		names = append(names, n)
	}
	sort.Strings(names)
	t.Logf("SQL ↔ Ent columns checked for %v", names)
}

// parseDDLColumns deliberately normalizes platform line endings before the
// narrow migration-004/005 format check. Git for Windows may store local
// untracked SQL files as CRLF even when the source patch uses LF.
func parseDDLColumns(ddl string) map[string]map[string]string {
	ddl = strings.ReplaceAll(ddl, "\r\n", "\n")
	block := regexp.MustCompile(`(?ms)^CREATE TABLE ([a-z_]+) \(\n(.*?)^\);`)
	col := regexp.MustCompile(`^\s*([a-z][a-z0-9_]*)\s+(uuid|text\[\]|text|jsonb|bigserial|bigint|integer|boolean|timestamptz|bytea|date|numeric\([0-9]+,[0-9]+\))(?:\s|,|$)`)
	tables := make(map[string]map[string]string)
	for _, m := range block.FindAllStringSubmatch(ddl, -1) {
		cols := make(map[string]string)
		for _, line := range strings.Split(m[2], "\n") {
			if v := col.FindStringSubmatch(line); v != nil {
				cols[v[1]] = v[2]
			}
		}
		tables[m[1]] = cols
	}
	return tables
}
