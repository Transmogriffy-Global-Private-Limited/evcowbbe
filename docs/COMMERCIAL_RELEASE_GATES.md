# EVCOWBBE commercial machine: safe installation and release gate

**Source baseline SHA:** `2fc43556b10c303c49594ceb7609604060525b7c`. **Owner branch:** `anubhab-work`. Do not commit/push on behalf of another branch and never alter old migrations.

## Supported operation right now

The attached artifact is an experimental source overlay intended for an engineer-owned **clean** checkout matching the exact baseline SHA. It includes a pure calculation engine, a non-statutory charge-statement presenter, proposed ORM models/migration 0010 and new Admin routes. Installer does not run migrations or touch the runtime, GitHub, HAL, VPS2, or live PostgreSQL.

## Installation (Windows PowerShell)

1. Preserve or commit your own outstanding work **first**. Do not reset a dirty repo.
2. Ensure `git branch --show-current` equals `anubhab-work` and `git rev-parse HEAD` equals the baseline SHA.
3. Extract the ZIP **outside** the repository, then from its `installer` directory run:

```powershell
$repo = "$HOME\Programs\My_Programs\evcowbbe"
py -3 .\apply.py --repo $repo
# Inspect dry-run output and confirm baseline, then:
py -3 .\apply.py --repo $repo --apply
```

4. Inspect `git status -sb`, `git diff --check`, `git diff -- cow/models.py cow/admin/router.py`, and new file list. No automatic commit.
5. Load the project's `.env` **only through the existing safe local workflow**, then run `python manage.py check` and `python manage.py makemigrations cow --check --dry-run`. The installer itself never loads secrets.
6. Run the included pure-domain tests and all pre-existing repository static suites.
7. Review the generated OpenAPI route names, typed schema parity, error mappings and 18 legacy Sprint7 operations. Run `scripts/verify_zone_swagger.py`; any failure is a blocker.
8. Use **only an explicit disposable PostgreSQL 17.5 database** for `migrate`/rollback and integration/concurrency tests. Confirm actual disposable DB identity and environment before running. Verify the complete migration history `0001..0010`, rollback to 0009 and forward-reapply, constraints, guard behavior and historical data safety. Never point this acceptance at the real dev/Aiven database.
9. Only after passing, review and approve source changes for commit on `anubhab-work`. Merging to main, developer DB migration, public API exposure and any customer binding feature require separate release authorization.

## Additional fault-path acceptance (partially complete)

- Parallel assignment of conflicting Zone defaults; one commit only.
- Parallel GST replacement and channel activation; never ready without coverage.
- Group membership reassignment under two competing requests; no double active membership.
- Future schedule cancellation/removal, rate/profile expiry and published-zone revision changes.
- Profile retirement/withdrawal while active assignments and quotes exist.
- Guarded migration rollback with real rows (no destructive down if historical data would be lost).
- `PricingPolicy` canonical ledger and `ZoneTariffPayload` legacy enforcement preserved.
- `CustomerProfile` membership and Admin RBAC DENY precedence, replayable idempotency.
- Unauthorized same-key request/body mismatches, conflicting writes and consistent error HTTP envelopes.
- Date/clock boundaries, timezone transitions, exact paise and large money bounds.
- OpenAPI route parity and typed output envelopes.
- Missing/ambiguous legal GST mapping, supplier identity, tax-class grouping, nonstatutory statement labels.

## Explicit blockers before using as live code

1. **Migration 0010 PostgreSQL triggers passed the disposable PG17.5 forward/empty reverse/reapply and catalog tests.** Applying to a persistent developer DB still requires positive target identity, approved backup and migration plan.
2. **Local Windows isolated Django/ORM and Admin acceptance have now run successfully**, but end-to-end customer quote/payment/HAL flows remain out of scope.
3. **Future scheduled override cancellation is deliberately not exposed yet.** Add a non-destructive cancellation state plus DB-level semantics before production scheduling UI.
4. **Commercial tax input is operator-reviewed configuration**, not statutory integration. Actual live tax correctness needs independent review and applicable seller/supply facts. Do not represent the commercial statement as a GST Tax Invoice.
5. **No customer binding serviceability/evidence/quote acceptance or ledger settlement integration.** No payout or wallet side effect is authorized; old 410 routes remain retired.
6. **The specified source and isolated PostgreSQL regression gates passed**, but full operational deployment and customer-side integration gates remain open.

## Rollback

Before an actual migration is applied, source rollback is straightforward because the installer leaves every file as an uncommitted diff/new file. Restore `cow/models.py` and `cow/admin/router.py` from your verified clean checkout and remove exactly the newly created overlay files only after inspecting the paths. The installation tool automatically restores these two originals on install-time exceptions. Do not use `git reset --hard` or bulk delete untracked files.

After migration 0010 is ever applied, **never** run an automatic down migration against historical/commercial data without a reviewed preservation plan. Reversal of tables destroys new records, even if old 0001-0009 history remains untouched. Prefer controlled forward correction with verified backups where durable records exist.

The historic VPS2 EVCOWBBE runtime was decommissioned; do not run the older `rehost-evcowbbe` VPS2 script. Work only in the approved local-first environment.
