# Sprint 6: Zone commercial registry persistence (source overlay, not runtime activation)

**Authority:** user-approved working branch `feature/mobile-qr-wallet-zone-driver-foundation-20261104` at local commit `dd12cd2` (Sprint 4 + Sprint 5 committed). Public `main` reference is `9cfcfe8b33ebf9ca196e8446753d1ecddf31a918`; the local feature branch is not assumed remotely readable. Normative scoped references: Annex 35 dispatch, Annex 36 pricing/tax/payment, Annex 40 customer/ServiceJob, Annex 43 time/precision. Later user decisions: driver-zone tariff ownership, zone GST assignment, primary kWh **OR** minute **OR** session, configurable extras, mobile and QR modes.

## Delivered, source only

- **Five additive Django ORM entities:** `ServiceZone` versioned radial operating zone; `DutyZoneAssignment` and `ChargerZoneAssignment` with half-open effective windows; `ZoneGSTAssignment` for channel-specific approved tax decision; `ZoneTariffPayload`, a strictly dependent typed commercial plan linked 1:1 to the **existing** `PricingPolicy`. The existing `PricingPolicy` remains the sole tariff publication/temporal ledger. The attached plan is a composite pricing payload; it is not another independent publication ledger.
- **Single additive migration** `cow/0008_zone_commercial_registry` depending on 0007, with reversible PostgreSQL guards for immutable version identities, approval states, actor plane check, overlapping duty/charger assignments and overlapping approved zone GST intervals. No earlier migration edited. No auto-DDL.
- **Read-only internal resolver** derives a zone revision from a committed duty assignment for roaming, or trusted charger assignment for QR, validates zone effective time/position evidence, selects one exact published tariff with the previously committed canonical `pricing_topology` resolver, selects an exact approved zone+channel GST record, and passes both through the existing `pricing_engine` via `evaluate_zone_tariff`. DB-backed evaluation is **UPFRONT preview only**; final outcomes require `evaluate_frozen()` with an already committed policy/tax snapshot rather than reselecting current DB. It has **no HTTP endpoints** and **never posts money or issues HAL commands**.
- **Conservative WGS84 radial classifier** takes a source-identified, timezone-aware verified location. It returns INSIDE, OUTSIDE or UNKNOWN when accuracy straddles the boundary or exceeds a trusted threshold. Haversine is never presented as road distance/ETA. Freshness enforced at the read-only resolver. The provider and dispatch/QR service must verify authenticity, ownership, proximity, exact authorization and stricter policy limits.

## Important product/architecture constraints

1. **Roaming service:** driver duty assignment owns the pricing Zone. Customer location or GPS does not reassign ownership. Before driver assignment, only a clearly labelled estimate is possible; the existing `jobs.create_quote()` must not be treated as the binding zone-priced customer quote. This slice does not alter its behavior.
2. **QR service:** charger/site assignment supplies the Zone, but being inside the Zone is not proof of scanning the QR near the charger. The resolver additionally requires upstream trusted QR and charger-specific proximity proof; no public route supplies this yet.
3. **GST:** the Zone binds a separately approved seller/classification/tax profile by service channel. Administrative Zone geography does NOT automatically establish place of supply, tax registration, GST classification or zero rate. New/changed legal profile needs verified tax decision; nothing in this artifact asserts actual Indian GST rates.
4. **Policy:** the `PricingPolicy` model's rule-based generic Admin API remains intact. `ZoneTariffPayload` stores the previously approved composite zone plan as an attached, immutable subtype. Only matching published policy scope `ZONE_ROAMING` / `ZONE_ON_SITE_QR` is eligible, and any unbound published revision fails closed. A future Admin authoring/publishing service must validate the composite plan, link it to a DRAFT policy atomically, recheck authorization and audit publication before this is customer-facing. The dual representation (`PricingPolicy.rules` vs composite payload) must be reconciled by that reviewed authoring service; this source overlay **does not claim that admin authoring works yet**.
5. **Temporal lifecycle:** service, zone/tax/assignment and policy use `[start,end)` windows. Assignment/tax revisions have a mandatory effective start and are append-only except reviewed forward closing of an open interval; closing should be audited by a future authoring service. No historic quote/settlement may dynamically reselect the policy after acceptance: snapshot full policy and tax decisions at the authoritative commitment instant.
6. **Wallet:** the Sprint 5 `plan_two_stage_collection()` remains an instruction-only helper. This package does not implement a wallet DB ledger, customer debit, refund, Razorpay capture, or financial reconciliation. Those must not be inferred from read-only pricing output.
7. **Operations:** no service redeployment, migration execution, production DB changes, live HAL, or rollout are included. The guarded VPS2 rehost operator rejects changed migration files by design; **do not bypass it**.

## Installation and verification (Windows PowerShell in repo PWD)

Install script preflights the expected branch, exact commit, a clean working tree, the canonical `cow/models.py` Git blob and the prior Sprint 4+5 code. It rejects any changed destination, verifies SHA256 manifest, copies only new source files, and appends **one import** to `cow/models.py` and updates the exact migration filename expectation in `tests/test_static_integrity.py` and extends `tests/test_migration_parity.py` to scan the additional model module. It does not Git-stage/commit, migrate or deploy.

1. Extract the ZIP to a separate folder, then run `& '<extracted>\scripts\install_overlay.ps1' -RepositoryRoot (Get-Location).Path`.
2. Review `git status --short`, `git diff --check`, `git diff --stat`, and read `cow/migrations/0008_zone_commercial_registry.py`.
3. With the local project Python/venv:

```powershell
python -m unittest discover -s tests -p 'test_zone_position.py' -v
python -m unittest discover -s tests -p 'test_zone_schema_contract.py' -v
python -m unittest discover -s tests -p 'test_service_commerce.py' -q
python -m unittest discover -s tests -p 'test_zone_tariff_convergence.py' -q
python manage.py check
python manage.py makemigrations --check --dry-run --noinput
python -m compileall -q cow tests
```

4. **Database acceptance requires a separate approval.** Positively identify a disposable PostgreSQL database and restricted roles, preserve a verified restorable backup, inspect the exact applied migration ledger (expect 0007), review the SQL forward and reverse paths, then apply 0008 only there. After that, run `python -m pytest tests/integration_zone_registry_pg.py -q` on the approved fixture. A real concurrent multi-connection overlap test and schema catalog inspection are still required. The current `scripts/run_pg_tests.py` was built for the earlier migration workflow; do not assume it has a reviewed 0007-to-0008 mode.
5. Do not merge/push/deploy or run against `evcowbbe_django_next` until migration acceptance, complete security/business authorization, snapshots, money posting/recovery, FE/OpenAPI work and operational approval are independently passed.

## Known limitations / verification debt

- Pure tests run in isolated Python. Django and PostgreSQL aren't installed in this packaging workspace. The model/migration equivalence, SQL triggers, concurrency, mutable DB upgrade and Django integration tests are **not verified here**. This is a reviewable code candidate, not an accepted DB release.
- Source-only overlay is intentionally additive. If the local checkout diverges from `dd12cd2`, installer stops; review and port changes manually, never override. No production database migration or service rehost is implied.
- An authoritative Admin API, positive state approvals, late-reassignment workflow, physical QR verification and delivery/settlement still need engineered interfaces and acceptance. Live features are not claimed.
- Existing `ServiceZone` only supports approved radius geometry. Polygon versioning and geospatial provider configuration are separate later domain options, not inferred from this migration.

## Recovery

Before committing, reverse the overlay by reverting the one added import in `cow/models.py`, restoring the `tests/test_static_integrity.py` migration list and the `tests/test_migration_parity.py` scanner and removing **only** its eight newly installed source/docs/test files. Do not reset/clean unrelated work. The package does not touch DB. On a test DB where migration 0008 was explicitly applied, rollback must follow a reviewed, backed-up migration reversal and is not part of source cleanup. Historical records and FK dependencies may make schema reversal data-destructive; default recovery is restore the exact verified backup, after assessing forward data divergence.
