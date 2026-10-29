// Package ent contains generated, strongly typed database accessors.
//
// Ent is the target modeling paradigm for the entire application. During the
// guarded Atlas cutover, the old SQL runner remains frozen at 006. Generation
// must never execute DDL; see docs/database/ENT_ATLAS_UNIFICATION.md.
package ent

//go:generate go run -mod=mod entgo.io/ent/cmd/ent generate --feature sql/versioned-migration ./schema
