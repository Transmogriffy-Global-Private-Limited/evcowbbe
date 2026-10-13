// A fixed, offline ELF identity inspector for the VPS2 Go release builder.
// Usage: GO111MODULE=off go run release_identity.go SERVER MIGRATOR SOURCE_SHA
// It does not open PostgreSQL or execute the supplied binaries.
package main

import (
	"debug/buildinfo"
	"debug/elf"
	"fmt"
	"os"
	"regexp"
)

const (
	modulePath = "github.com/Transmogriffy-Global-Private-Limited/evcowbbe"
	shaSymbol  = modulePath + "/internal/buildinfo.GitSHA.str"
)

var shaPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)

func verify(path, expectedSHA, expectedPackage string) error {
	info, err := buildinfo.ReadFile(path)
	if err != nil {
		return fmt.Errorf("Go build metadata unavailable: %w", err)
	}
	if info.Path != expectedPackage {
		return fmt.Errorf("Go package identity mismatch")
	}

	file, err := elf.Open(path)
	if err != nil {
		return fmt.Errorf("Linux ELF unavailable: %w", err)
	}
	defer file.Close()

	symbols, err := file.Symbols()
	if err != nil {
		return fmt.Errorf("ELF symbol table unavailable: %w", err)
	}

	found := false
	for _, sym := range symbols {
		if sym.Name != shaSymbol {
			continue
		}
		if found {
			return fmt.Errorf("duplicate embedded Git SHA symbol")
		}
		found = true
		for _, program := range file.Progs {
			if program.Type != elf.PT_LOAD || sym.Value < program.Vaddr {
				continue
			}
			offset := sym.Value - program.Vaddr
			if offset > program.Filesz || uint64(len(expectedSHA)) > program.Filesz-offset {
				continue
			}
			bytes := make([]byte, len(expectedSHA))
			if _, err := program.ReadAt(bytes, int64(offset)); err != nil {
				return fmt.Errorf("embedded Git SHA unreadable: %w", err)
			}
			if string(bytes) != expectedSHA {
				return fmt.Errorf("embedded Git SHA mismatch")
			}
			return nil
		}
		return fmt.Errorf("embedded Git SHA is outside file-backed ELF segments")
	}
	return fmt.Errorf("embedded Git SHA symbol absent")
}

func main() {
	if len(os.Args) != 4 || !shaPattern.MatchString(os.Args[3]) {
		fmt.Fprintln(os.Stderr, "expected server, migrator and exact lowercase 40-character source SHA")
		os.Exit(2)
	}
	for _, item := range []struct {
		path, pkg string
	}{
		{os.Args[1], modulePath + "/cmd/server"},
		{os.Args[2], modulePath + "/cmd/migrate"},
	} {
		if err := verify(item.path, os.Args[3], item.pkg); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
	fmt.Println("paired_embedded_release_identity=verified")
}
