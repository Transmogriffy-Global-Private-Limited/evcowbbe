package schema

import (
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"

	"entgo.io/ent/dialect/entsql"
)

var historicalMigrations = []string{
	"001_auth.sql", "002_outbox_admin_rbac.sql", "003_fleet_inventory.sql",
	"004_customer_foundation.sql", "005_admin_pricing_rules.sql",
}

// TestHistoricalChecksRepresentedInEnt prevents a future Ent/Atlas diff from
// silently weakening SQL-only policies: the Ent model must declare *every*
// historical CHECK, including expression and multi-column constraints.
func TestHistoricalChecksRepresentedInEnt(t *testing.T) {
	actual := make(map[string][]string)
	create := regexp.MustCompile(`(?i)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)\s*\(`)
	check := regexp.MustCompile(`(?i)\bCHECK\s*\(`)
	for _, file := range historicalMigrations {
		text := historicalDDL(t, file)
		for _, m := range create.FindAllStringSubmatchIndex(text, -1) {
			table := text[m[2]:m[3]]
			definition, _ := sqlParens(t, text, m[1]-1)
			for _, found := range check.FindAllStringIndex(definition, -1) {
				body, _ := sqlParens(t, definition, found[1]-1)
				actual[table] = append(actual[table], compactSQL(body))
			}
		}
	}
	for table, expressions := range actual {
		if table == "schema_migrations" { // metadata, not an Ent entity
			continue
		}
		want := make([]string, 0, len(sqlChecks[table]))
		for _, check := range sqlChecks[table] {
			want = append(want, compactSQL(check))
		}
		sort.Strings(want)
		sort.Strings(expressions)
		if strings.Join(expressions, "\n") != strings.Join(want, "\n") {
			t.Errorf("%s: SQL CHECK expressions differ from Ent annotations:\nSQL: %v\nEnt: %v", table, expressions, want)
		}
	}
	for table, checks := range sqlChecks {
		if len(checks) > 0 && len(actual[table]) == 0 {
			t.Errorf("%s: Ent contains checks but historical SQL has none", table)
		}
	}
}

// TestHistoricalIndexesRepresentedInEnt asserts all operational CREATE INDEX
// objects appear in the model with their reviewed names. Predicate/ordering
// requirements are independently retained in sqlIndexes source annotations.
func TestHistoricalIndexesRepresentedInEnt(t *testing.T) {
	indexPattern := regexp.MustCompile(`(?im)\bCREATE\s+(UNIQUE\s+)?INDEX\s+([a-z_]+)\s+ON\s+([a-z_]+)\s*\(([^)]*)\)\s*(?:WHERE\s+([^;]+))?;`)
	seen := make(map[string]bool)
	// These are table-level UNIQUE constraints, which PostgreSQL backs with
	// indexes but does not spell as independent CREATE INDEX statements.
	constraintIndexes := map[string]struct{ file, expression string }{
		"customer_vehicles_id_customer_id_key":           {"004_customer_foundation.sql", "UNIQUE(id,customer_id)"},
		"customer_addresses_id_customer_id_key":          {"004_customer_foundation.sql", "UNIQUE(id,customer_id)"},
		"pricing_policies_policy_key_policy_version_key": {"005_admin_pricing_rules.sql", "UNIQUE (policy_key,policy_version)"},
	}
	for name, source := range constraintIndexes {
		if !strings.Contains(historicalDDL(t, source.file), source.expression) {
			t.Fatalf("SQL no longer declares expected UNIQUE constraint index %s", name)
		}
		seen[name] = true
	}
	for _, file := range historicalMigrations {
		for _, matches := range indexPattern.FindAllStringSubmatch(historicalDDL(t, file), -1) {
			name, table := matches[2], matches[3]
			if seen[name] {
				t.Fatalf("duplicate index %s", name)
			}
			seen[name] = true
			found := false
			for _, idx := range sqlIndexes[table] {
				if idx.Descriptor().StorageKey != name {
					continue
				}
				found = true
				got := idx.Descriptor()
				if got.Unique != (strings.TrimSpace(matches[1]) != "") {
					t.Errorf("%s: UNIQUE property differs", name)
				}
				var wantCols []string
				var wantDesc []string
				for _, col := range strings.Split(matches[4], ",") {
					parts := strings.Fields(strings.TrimSpace(col))
					if len(parts) == 0 {
						t.Fatalf("%s: empty index column", name)
					}
					wantCols = append(wantCols, parts[0])
					if len(parts) > 1 && strings.EqualFold(parts[1], "DESC") {
						wantDesc = append(wantDesc, parts[0])
					}
				}
				if strings.Join(got.Fields, ",") != strings.Join(wantCols, ",") {
					t.Errorf("%s: columns SQL=%v Ent=%v", name, wantCols, got.Fields)
				}
				var annotation entsql.IndexAnnotation
				for _, a := range got.Annotations {
					if x, ok := a.(*entsql.IndexAnnotation); ok {
						annotation = *x
					}
				}
				if compactSQL(annotation.Where) != compactSQL(strings.TrimSpace(matches[5])) {
					t.Errorf("%s: partial predicate SQL=%q Ent=%q", name, matches[5], annotation.Where)
				}
				for _, field := range wantCols {
					want := false
					for _, d := range wantDesc {
						if d == field {
							want = true
						}
					}
					if annotation.DescColumns[field] != want {
						t.Errorf("%s: DESC ordering differs for %s", name, field)
					}
				}
			}
			if !found {
				t.Errorf("%s: missing historical index %s in Ent", table, name)
			}
		}
	}
	for _, indexes := range sqlIndexes {
		for _, idx := range indexes {
			if !seen[idx.Descriptor().StorageKey] {
				t.Errorf("Ent index %s missing from historical CREATE INDEX catalog", idx.Descriptor().StorageKey)
			}
		}
	}
}

func historicalDDL(t *testing.T, name string) string {
	t.Helper()
	buf, err := os.ReadFile("../../migrations/" + name)
	if err != nil {
		t.Fatal(err)
	}
	return strings.ReplaceAll(string(buf), "\r\n", "\n")
}

// sqlParens scans nested SQL parentheses without mistaking quoted strings for
// structural tokens; it returns the contents of the opening parenthesis.
func sqlParens(t *testing.T, s string, open int) (string, int) {
	t.Helper()
	if open < 0 || open >= len(s) || s[open] != '(' {
		t.Fatalf("expected opening SQL parenthesis at %d", open)
	}
	depth := 0
	inString := false
	for i := open; i < len(s); i++ {
		switch s[i] {
		case '\'':
			if inString && i+1 < len(s) && s[i+1] == '\'' {
				i++
			} else {
				inString = !inString
			}
		case '(':
			if !inString {
				depth++
			}
		case ')':
			if !inString {
				depth--
				if depth == 0 {
					return s[open+1 : i], i + 1
				}
			}
		}
	}
	t.Fatal("unclosed SQL parenthesis")
	return "", 0
}

func compactSQL(s string) string {
	var out strings.Builder
	inString := false
	for i := 0; i < len(s); i++ {
		if s[i] == '\'' {
			inString = !inString
		}
		if inString || (s[i] != ' ' && s[i] != '\t' && s[i] != '\n' && s[i] != '\r') {
			out.WriteByte(s[i])
		}
	}
	return out.String()
}
