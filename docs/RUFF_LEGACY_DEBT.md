# Controlled legacy Ruff lint debt (2026-11-04)

The Swagger UI repair introduced a **strict lint gate for all touched source**,
while retaining pre-existing Ruff findings in unchanged legacy files. Applied
Django migrations and historical verification snapshots are deliberately not
rewritten merely to satisfy style tooling.

The exact allowed legacy debt is in
`docs/verification/ruff-legacy-lint-debt-20261104.json` and the Ruff
`[tool.ruff.lint.per-file-ignores]` table. Every ignored file's SHA-256 is
pinned by `tests/test_ruff_legacy_debt.py`; editing any such file without
reconciling its debt fails the test suite. No glob exceptions are permitted.
New and changed Swagger files have no lint exceptions.

For later maintenance, eliminate the relevant Ruff findings while modifying a
pinned file, then remove or narrow its per-file exceptions and update the
manifest and test consistently. Review changes to applied migration files
separately: the release manager forbids automatic schema transitions and
checks the migration lineage for identity.

`ruff check .` should pass on the finished repository, **but this does not
claim the older files are style-clean**. The historical debt is documented,
not silently erased. Never use `ruff --fix .` or global rule suppressions as a
replacement for application-level regression tests.
