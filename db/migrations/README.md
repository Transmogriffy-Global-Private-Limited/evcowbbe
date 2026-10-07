# Database migrations

This directory contains immutable, ordered, forward-only application schema
migrations. It is intentionally empty while the application has no domain
tables. The migration ledger is initialized by `cmd/migrate up`.

When the first application migration is needed, add one SQL file named
`<positive-integer>_<lowercase_name>.sql`, for example
`000001_create_example.sql`. Every application migration must contain exactly
one compatibility declaration on its own line:

```sql
-- evcowbbe:min-compatible-binary-version=0
```

The value is the oldest binary schema version that may operate after the
migration. It must be an integer from `0` through the migration version,
inclusive. The directive is part of the SQL file checksum. Never edit, rename,
delete, or reuse a version after that migration has been applied anywhere.
Create a new forward migration instead.
