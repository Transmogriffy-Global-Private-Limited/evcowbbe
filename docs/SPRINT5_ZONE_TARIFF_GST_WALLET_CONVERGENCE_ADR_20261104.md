# EVCOWBBE: zone tariff / GST / collection convergence (additive source slice)

**Date:** 2026-11-04. **Status:** Implemented pure-code adapter; **NOT** activated charging, wallet collection, Django migration, or production billing.

## Authority and baseline

- Current EVCOWBBE: `Transmogriffy-Global-Private-Limited/evcowbbe`, `main`, Django canonical; review baseline GitHub `9cfcfe8b33ebf9ca196e8446753d1ecddf31a918`. The user's local branch also contains their **already-applied** `evcowbbe-mobile-qr-wallet-zone-driver-foundation-20261104.zip` (60 prior pure tests), whose bytes were not available here. This extension creates **new filenames only**, does not replace `cow/domain/service_commerce.py`, and makes no assumption about its function signatures.
- New CMS reference: `Transmogriffy-Global-Private-Limited/ev-cms-backend-new`, Go `main` reviewed at `dafcab3f11f403f4a70fd0d9f24064f3b873f3af`. Its `commercial/tariff_temporal.go`, `customerauth/pricing.go`, `charging.go`, `charging_facts.go`, `razorpay.go`, and invoice module informed this design.
- Older normative blueprint, especially Annexes 35, 36, 40, 42 and 43; newer user decisions supersede baseline commercial assumptions where they differ.

## What is coded in this archive

`cow/domain/zone_tariff_convergence.py` is an additive **adapter to the existing** `cow.services.pricing_engine.evaluate` and `cow.services.pricing_topology.resolve`. There is **no second arithmetic engine or second timeline resolver**.

The single versioned **Zone + Channel** plan contains an INSIDE profile and, when authorized, an OUTSIDE profile. Each profile contains one primary charging basis (fixed rate per kWh **OR** minute **OR** session), fixed transport, minimum-billable distance with **progressive** distance slabs, and optional fixed event charges (including configurable penalties) or timed waiting fees. Charge applicability is explicit for `UPFRONT`, `COMPLETED`, `CANCELLED`, and `NO_SHOW`. Fixed fees and their source identities are first-class line items; no accidental penalties on completed service. Time charging and waiting require an explicit whole-minute rounding choice. There is no silently invented fractional-minute policy.

The pure `select_published_zone_plan()` wrapper projects versioned entries into the **existing** nested temporal resolver. Exact scope kind is `ZONE_ROAMING` or `ZONE_ON_SITE_QR`; their tariff timelines cannot collide. Policy selection is as of one **trusted, timezone-aware commercial commitment instant**, not an implicit mutable `now()` on later reconciliation. A caller freezes the selected policy in the accepted quote/job/charging context; settlement evaluates that same frozen policy with actual facts. Published candidates must come from an authorized repository, not a browser-provided list.

`TrustedCommercialContext` binds the pricing zone from either the **serving driver's approved duty assignment** (roaming) or the **trusted charger/site** (on-site QR). STRICT/ADVISORY and actual inside/outside geography must be verified by the serviceability/dispatch layer. `OUTSIDE` is not permitted by default; a positive explicit eligibility decision is required. Location distance/ETA must be sourced from trusted route evidence, not Haversine masquerading as actual travel. Neither GPS position nor customer location automatically transfers tariff ownership.

`ZoneGSTBinding` is an explicit **zone + channel-to-approved-tax-profile reference** (preventing a roaming seller/tax profile from silently applying to QR services), with effective dates, revision, seller tax reference and legal-decision provenance. The zone is an administrative selection key; actual GST applicability and legal place-of-supply must be reviewed and supplied by the tax authority/provider. Each GST tax class is validated (no simultaneous IGST and split SGST/CGST). One unique effective Zone GST binding is required. Missing/ambiguous/unapproved profiles fail closed: **zero tax is not inferred**. A frozen copy is attached to the price result.

`plan_two_stage_collection()` produces **instructions only**, with stable per-job idempotency keys: `cow:upfront:<uuid>`, `cow:final:<uuid>`, `cow:credit:<uuid>`. Upfront collection is the configured transport plus minimum-billable distance. Final payable is evaluated from the frozen policy, actual route/HAL facts, tax and applicable extra charges; net additional debit = `max(final - posted upfront, 0)` and net customer credit = `max(posted upfront - final, 0)`. It does not double-count upfront transport. **Only an actual posted upfront ledger receipt** allows final net planning. Posting, wallet locks, cross-service transaction recovery, partial delivery, refunds, and invoice generation are **deliberately not implemented** by this pure module.

## Precise remaining integration work (not hidden)

1. **Django persistence and admin APIs:** add reviewed `ServiceZone`, versioned driver-duty/zone assignment, Zone-to-GST-profile binding, canonical tariff plan terms/publication and tax snapshots. Use Django migrations as schema ledger. Avoid changing live DB until isolated PostgreSQL backup, schema and rollback review. Do not overwrite existing `PricingPolicy` legacy paths without compatible migration and FE review.
2. **Trusted runtime adapters:** apply zone geography/STRICT-ADVISORY decisions, service mode, serving-driver commitment timing, QR proximity, legal tax approval, and authoritative route/HAL fact provenance. Establish context server-side; never deserialize `TrustedCommercialContext` from the client.
3. **Quote lifecycle:** existing `cow.services.jobs.create_quote` currently uses old `PricingPolicy(status='ACTIVE')` and cannot determine a driver's zone before assignment. Introduce estimate versus binding quote/acceptance sequence, version/expiry/consent; do not pretend the new published-policy resolver is already wired.
4. **Money ledger:** implement one atomic upfront debit linked to the job, reservation/coverage for uncertain charging where required, idempotent final debit or credit/refund, balance/hold concurrency locking, and durable reconciliation for provider/HAL unknowns. Stable keys alone are insufficient without DB uniqueness, authoritative posting, and recovery workers.
5. **Physical charging:** keep HAL command accepted vs actual OCPP start/completion distinct. Freeze pricing before first charge, aggregate multiple physical sessions under one job settlement, and stop/reconcile on missing telemetry. No physical HAL commands are enabled by this archive.
6. **Invoices:** after job **financial** finality, invoice from frozen line items, tax context and issuer identity. Immutable artifact with checksum, durable delivery and audit corrections, not a mutable rerender.
7. **Independent driver compensation:** based on its own approved rules and ledger, not derived automatically from customer tariff components.
8. **Full acceptance:** real current-pricing-engine unit tests, isolated PostgreSQL schema/unit/integration/race tests, original 60 tests, FE OpenAPI compatibility, replay/idempotency/wallet/QR/HAL tests. Never equate passing pure tests with ready-to-deploy billing.

## Known intentional limits

- `ON_SITE_QR` cannot levy roaming transport or distance fees. A configurable on-site fixed fee may be expressed through `extras`.
- Progressive distance tiers are linear **per km**. No implicit per-trip *flat-tier* replacement or kilometre rounding; a future policy wanting flat tiers needs an explicit new variant and regression fixtures.
- The permitted time billed quantity rounds completed seconds **up or down to whole minutes** according to an explicit policy field; exact fractional-minute billing is **not** claimed. Product and legal owners must approve the chosen policy.
- No daily recurring peak/off-peak clock bands yet; temporal publication windows and charging duration are separate concepts. Do not silently reinterpret one as the other.
- This adapter supports a primary charging basis and optional static/event/waiting extras; full modifier/discount rule authoring remains available in the existing pricing engine but is **not yet exposed** by this narrow adapter. Future support should compile into existing rules, not create another money calculator.
- The framework's `FINAL` mode blocks estimated facts. This adapter rejects estimated facts for completed/cancelled evaluation itself; `UPFRONT` only uses contractually frozen amounts.
- No customer/driver credentials, payment-provider secrets, example real GST rates, phone locations, or VPS access are embedded.

## Installation into the already-patched local tree

The files are entirely **new**. Do **not** re-apply the previous Sprint 4 ZIP. Do **not** copy an `AGENTS.md` or overwrite the local Django settings/model files.

From a local **feature branch** of EVCOWBBE, back up/commit existing uncommitted work first; check `git status --short`, then run the included `scripts/install_additive.ps1` with the repo root argument. It preflights destinations and refuses to overwrite existing source. Or copy only:

- `cow/domain/zone_tariff_convergence.py`
- `tests/test_zone_tariff_convergence.py`
- `docs/SPRINT5_ZONE_TARIFF_GST_WALLET_CONVERGENCE_ADR_20261104.md`

Run, with your existing project virtualenv:

```powershell
python -m unittest discover -s tests -p 'test_service_commerce.py' -v
python -m unittest discover -s tests -p 'test_zone_tariff_convergence.py' -v
python -m unittest discover -s tests -p 'test_*.py' -q
python manage.py check
python manage.py makemigrations --check --dry-run --noinput
git diff --check
git status --short
```

The first test verifies the prior applied 60-test package; the second runs new tests including **real** engine and timeline integration when the repo's canonical modules are importable. The code has no migration or deploy hooks. The new tests' arithmetic double is explicitly **test-only**; it is not shipped as an application billing engine.

**Stop conditions:** destination file already exists, missing prior foundation, failed canonical engine test, changed migration state, source branch unexpected, unreviewed tax treatment, a wallet/HAL gap. Preserve evidence and resolve before further integration.

## Recovery

For the pure additive slice, remove *only these three newly added files* if not already referenced by other work. No DB rollback or service restart is needed because none is performed. If further commits/integration depend on this module, remove callers first and re-run all regressions. Never reset or force-clean the broader local tree to undo this package.
