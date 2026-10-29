package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCanonicalSQLBytes(t *testing.T) {
	for _, input := range [][]byte{
		[]byte("SELECT 1;\nSELECT 2;\n"),
		[]byte("SELECT 1;\r\nSELECT 2;\r\n"),
		[]byte("SELECT 1;\r\nSELECT 2;\n"),
	} {
		got, err := canonicalSQLBytes(input)
		if err != nil || string(got) != "SELECT 1;\nSELECT 2;\n" {
			t.Fatalf("LF normalization: got %q, error %v", got, err)
		}
	}
	if _, err := canonicalSQLBytes([]byte("SELECT 1;\rSELECT 2;")); err == nil {
		t.Fatal("bare carriage return must not be silently accepted")
	}
}

// Exercise the actual one-time baseline verifier as though Git had checked
// out pre-.gitattributes files with mixed Windows EOLs. Source and mirror may
// differ only by CRLF/LF; all other bytes still require pinned SHA-256.
func TestAtlasBaselineAgainstMixedWindowsWorktree(t *testing.T) {
	fixture := t.TempDir()
	for _, dir := range []string{"migrations", "internal/database/migrations", atlasDir} {
		if err := os.MkdirAll(filepath.Join(fixture, dir), 0755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(fixture, "go.mod"), []byte("module baselinefixture\n"), 0644); err != nil {
		t.Fatal(err)
	}
	for i, item := range immutableHistory {
		input, err := os.ReadFile(filepath.Join("..", "..", "migrations", item.name))
		if err != nil {
			t.Fatal(err)
		}
		canonical, err := canonicalSQLBytes(input)
		if err != nil {
			t.Fatal(err)
		}
		front := canonical
		back := canonical
		if i%2 == 0 {
			front = bytes.ReplaceAll(canonical, []byte("\n"), []byte("\r\n"))
		}
		if i%3 == 1 {
			back = bytes.ReplaceAll(canonical, []byte("\n"), []byte("\r\n"))
		}
		if err := os.WriteFile(filepath.Join(fixture, "migrations", item.name), front, 0644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(fixture, "internal/database/migrations", item.name), back, 0644); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{atlasFile, "atlas.sum"} {
		body, err := os.ReadFile(filepath.Join("..", "..", atlasDir, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(fixture, atlasDir, name), body, 0644); err != nil {
			t.Fatal(err)
		}
	}
	t.Chdir(fixture)
	if err := run(); err != nil {
		t.Fatalf("baseline must accept equivalent mixed CRLF/LF worktrees: %v", err)
	}
	// A Windows checkout can also CRLF-convert the *generated* Atlas baseline
	// or atlas.sum. These may be restored atomically to the pinned LF version,
	// but only after both canonical contents are verified in advance.
	generated := map[string][]byte{}
	for _, name := range []string{atlasFile, "atlas.sum"} {
		path := filepath.Join(atlasDir, name)
		original, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		generated[name] = original
		windows := bytes.ReplaceAll(original, []byte("\n"), []byte("\r\n"))
		if err := os.WriteFile(path, windows, 0644); err != nil {
			t.Fatal(err)
		}
	}
	if err := run(); err != nil {
		t.Fatalf("equivalent Windows Atlas baseline/manifest must be canonicalized safely: %v", err)
	}
	for name, original := range generated {
		actual, err := os.ReadFile(filepath.Join(atlasDir, name))
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(actual, original) {
			t.Fatalf("generated %s was not restored to the pinned canonical bytes", name)
		}
	}
	baselinePath := filepath.Join(atlasDir, atlasFile)
	badBaseline := bytes.Replace(generated[atlasFile], []byte("Immutable SQL foundation snapshot"), []byte("Mutated SQL foundation snapshot"), 1)
	if err := os.WriteFile(baselinePath, badBaseline, 0644); err != nil {
		t.Fatal(err)
	}
	if err := run(); err == nil || !strings.Contains(err.Error(), "baseline differs beyond line endings") {
		t.Fatalf("changed Atlas baseline must be rejected without overwrite, got %v", err)
	}
	unchanged, err := os.ReadFile(baselinePath)
	if err != nil || !bytes.Equal(unchanged, badBaseline) {
		t.Fatal("rejected Atlas baseline mutation must not be overwritten")
	}
	if err := os.WriteFile(baselinePath, generated[atlasFile], 0644); err != nil {
		t.Fatal(err)
	}
	// A non-EOL change, even though both mirrors then agree, must be rejected.
	path := filepath.Join("migrations", immutableHistory[1].name)
	invalid, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	invalid = bytes.Replace(invalid, []byte("CREATE TABLE"), []byte("CREATE TABLZ"), 1)
	if err := os.WriteFile(path, invalid, 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join("internal/database/migrations", immutableHistory[1].name), invalid, 0644); err != nil {
		t.Fatal(err)
	}
	if err := run(); err == nil || !strings.Contains(err.Error(), "checksum drift") {
		t.Fatalf("changed SQL must fail pinned checksum, got %v", err)
	}
}
