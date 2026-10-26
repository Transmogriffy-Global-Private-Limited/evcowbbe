// Package contracts embeds the canonical OpenAPI document in the server binary.
// It is the same file maintained under docs/contracts, not a generated copy.
package contracts

import (
	"bytes"
	_ "embed"
)

//go:embed auth.openapi.yaml
var document []byte

// AuthOpenAPI returns a copy of the source embedded at build time.
func AuthOpenAPI() []byte { return bytes.Clone(document) }
