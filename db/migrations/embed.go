package migrationfiles

import "embed"

// FS exposes repository-visible migration SQL to the migration runtime.
//
//go:embed all:*
var FS embed.FS
