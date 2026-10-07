# Database migrations

This directory contains immutable, ordered, forward-only application schema
migrations. It is intentionally empty while the application has no domain
tables. The migration ledger is initialized by `cmd/migrate up`.

When the first application migration is needed, add one SQL file named
`<positive-integer>_<lowercase_name>.sql`, for example
`000001_create_example.sql`. Never edit, rename, delete, or reuse a version
after that migration has been applied anywhere. Create a new forward migration
instead.
