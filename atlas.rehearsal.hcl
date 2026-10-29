// Only for Work Package 3's independently provisioned PostgreSQL 18 test DBs.
// URLs are deliberately absent from this file and must be provided explicitly
// via isolated environment variables. Never use this configuration for VPS2.

env "rehearsal_fresh" {
  url = getenv("EVCOWBBE_ATLAS_FRESH_URL")
  migration {
    dir = "file://ent/migrate/migrations"
  }
}

env "rehearsal_populated" {
  url = getenv("EVCOWBBE_ATLAS_POPULATED_URL")
  migration {
    dir = "file://ent/migrate/migrations"
  }
}

env "rehearsal_diff" {
  url = getenv("EVCOWBBE_ATLAS_POPULATED_URL")
  src = "ent://ent/schema"
  dev = getenv("EVCOWBBE_ATLAS_DEV_URL")
  migration {
    dir = "file://ent/migrate/migrations"
    exclude = ["schema_migrations"]
  }
}
