// One developer-facing database workflow: Ent models + reviewed Atlas SQL.
// NEVER run schema apply, auto-DDL, or migration commands against VPS2 without
// the separate protected release authorization documented under docs/database.
//
// EVCOWBBE_ATLAS_DEV_URL must point at an empty, disposable PostgreSQL DB that
// Atlas is allowed to reset while replaying the immutable baseline.
// There is deliberately NO default target URL. An authorized database apply
// must supply --url explicitly, following an independent target preflight.

env "local" {
  src = "ent://ent/schema"
  dev = getenv("EVCOWBBE_ATLAS_DEV_URL")

  migration {
    dir     = "file://ent/migrate/migrations"
    // Ledger 001-006 belongs to historical audit. It is not an Ent entity.
    // Atlas keeps its separate atlas_schema_revisions table automatically.
    exclude = ["schema_migrations"]
  }
}
