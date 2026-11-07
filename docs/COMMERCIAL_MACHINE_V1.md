# EVCOWBBE commercial machine v1: executable source contract

**Baseline:** `Transmogriffy-Global-Private-Limited/evcowbbe` `main/anubhab-work` at `2fc43556b10c303c49594ceb7609604060525b7c` (verified 2026-11-07).
**Product decision:** 2026-11-07. This supersedes the Zone-owned tariff/GST *design for new authoring*, not historic migrations or transaction records.
**Status (2026-11-07):** installed and verified on an isolated PostgreSQL 17.5 cluster: migration 0010 forward/empty reverse/reapply, 7 commercial tables/12 guards, 51 integration tests passing (one historical upgrade test skipped), 29 commercial unit tests passing and 9 commercial acceptance markers passing. These results do **not** establish deployment to the persistent development database or customer quote/settlement/HAL authority.

## 1. Immutable responsibility boundaries

```
Approved tariff versions + assignments   Verified Zone/channel/group and service evidence
                  \                     /
                   -> Scope resolver -> selected tariff version
Approved GST versions + Zone assignments + seller/supply legal facts
                   -> GST resolver  -> applicable approved tax treatment
Resolved definitions + trusted quantities + lifecycle event
                   -> deterministic modular fare calculator
                   -> priced components + grouped tax heads + exact reconciliation
                   -> PREVIEW / FINAL_CALCULATED, always explainable
                   -> separate authorized quote/settlement (NOT in this overlay)
                   -> commercial charge statement (NOT A GST TAX INVOICE)
```

1. `cow/domain/commercial_machine.py` owns pure business validation, target/temporal selection, component calculation, GST extraction/addition and complete breakdown. It performs **no** DB, provider, wallet or HAL I/O.
2. `cow/domain/commercial_statement.py` renders the **existing** frozen result; never recalculates or claims settlement without a matching confirmed settlement fact. It never issues a statutory tax invoice.
3. `cow/services/commercial_admin.py` provides Admin-only configuration lifecycle, immutable publication, Group membership, target assignment, atomic replacement, Zone readiness and **nonbinding** scenario preview. All writes are atomic and audited.
4. `cow/admin/commercial.py` exposes strict, typed *Admin* requests under `/api/v1/admin/commercial`. Business writes use the existing authenticated role plane and idempotency key. Do not expose these paths to the customer.
5. New ORM models are inserted into canonical `cow/models.py` by a guarded installer. Migration `0010_commercial_machine_registry` is additive. **Do not edit 0001-0009.** Existing `PricingPolicy` remains the sole tariff version/publication ledger (`scope.kind=LIBRARY_COMPOSITE`, exact key/version scope, `rules=[]` and a required immutable `CommercialTariffPayload`). Old `ZoneTariffPayload` remains historical and its old endpoints are preserved.
6. No location provider, duty candidate, route provider, QR verifier, invoice authority or payment authorization is forged. Earlier legacy customer quote routes remain 410; there is no automatic switch to these new Admin preview definitions.

## 2. States and authority

| Entity | States | Transition/access requirement |
|---|---|---|
| Reusable tariff version (PricingPolicy) | `DRAFT -> PUBLISHED -> WITHDRAWN` | Only approved Admin mutation; published price terms immutable; withdrawal must not break assignments |
| GST Profile version | `DRAFT -> APPROVED -> RETIRED/REVOKED` | Admin pricing + invoice capability; recorded legal decision reference; immutable tax terms once recorded |
| Tariff assignment | Default or scheduled; effective by half-open window | Exactly one effective default per target/channel; Group price overrides Zone default; Zone always retains its own default |
| GST assignment | Effective by half-open window | Exactly one effective approved profile per Zone/channel; never a User Group GST shortcut |
| Group membership | One effective Group per customer | Historical memberships retained; no overlap |
| Zone/channel | Disabled or Enabled | Enabled requires published Zone, unbounded default tariff and unbounded approved GST; API readiness also checks tax-class coverage |
| Fare calculation | `PREVIEW_ONLY` or `FINAL_CALCULATED_NOT_SETTLED` | Final needs trusted server context and nonestimated required measurements; neither mode can itself charge |
| Statement | `ESTIMATE`, `FINAL_UNSETTLED`, or `SETTLED` | The last requires a matching confirmed external settlement fact; **all** remain non-statutory documents |

`ServiceZone.status=PUBLISHED` is still a geometry/administrative publication concept; `CommercialZoneChannel.enabled` is an explicit separate gate for new commercial readiness. This prevents the migration from misclassifying existing published Zones as automatically ready, preserving earlier records.

## 3. Selection decision tree

```
Verified operational Zone + supported channel?
  no -> SERVICE_UNAVAILABLE
  yes -> one effective Zone default guaranteed?
    no -> ZONE_DEFAULT_REQUIRED
    yes -> one applicable current Group tariff?
      yes -> choose Group version (do not stack)
      no  -> choose Zone default/its scheduled replacement
  verify selected plan supports channel and inside/outside relation
  exactly one matching GST assignment and APPROVED seller-specific profile?
    no -> GST_COVERAGE_INVALID/GST_NOT_APPROVED/GST_SELLER_MISMATCH
  do all triggered components resolve required measurements?
    no -> MEASUREMENT_MISSING/UNVERIFIED_MEASUREMENT
  validate tax classes, legally approved supply group and tax-head structure
  evaluate, allocate integer paise tax by supply group, reconcile
  return complete result with selection, lines, totals, disclosures, version
```

The operator creates a tariff once, assigns existing version to multiple Zones or Groups, duplicates to an independent version when customization is required, replaces atomically, and removes only the specified target when its invariants allow. An operational Zone's default must remain even if every active customer happens to have a Group override.

**Time rule:** `[starts_at, ends_at)`. Exact end boundary is not active. `DEFAULT` assignments can be closed and replaced at one instant; `SCHEDULED` overrides are finite, non-overlapping and require continuous default coverage. `CommercialZoneChannel.enabled` requires a current open-ended default/GST configuration; deliberate pause is required before removing final coverage. The implementation presently refuses premature cancellation of a future scheduled override; see release gates.

## 4. Bounded components

| Block | Supported config and dependencies | Output |
|---|---|---|
| Charging | Exactly one basis: `ENERGY_KWH`, `TIME_MINUTE` with rounding, or `SESSION` with verified session fact | Charging line |
| Visit | `ROAMING`; rate + permitted event set | Fixed visit line |
| Progressive distance | `ROAMING`; at most 32 strictly increasing slabs, last unbounded, integer meters | One line per slab, including zero-quantity lines |
| Fixed extra | Unique named event, permitted event set, nonnegative bounded rate | Named event line |
| Waiting-minute extra | Grace in 0..86,400 seconds; specified rounding; trusted seconds | Named waiting line |
| Price inclusion | Tariff default `INCLUSIVE/EXCLUSIVE`, optional per-block override | Extract GST or add GST, never twice |
| GST | Named approved tax classes grouped by approved supply group; CGST+SGST, CGST+UTGST, IGST or explicit zero; non-taxable classes distinguished | Tax-head ledger by component and total |

Rates are decimal strings, never JSON floats. Wh/metres/seconds are integers. Max 48 extras. Currency INR only in this version. Invalid/unknown keys and shapes are rejected rather than silently ignored. User-authored expressions, SQL, arbitrary rules and unbounded nesting are **not** supported.

**Not yet enabled:** generic discounts, minima, caps, dynamic time modifiers, B2B reverse charge, multiple tax suppliers on one job, statutory tax rate inference and automatic legal classifications. These require distinct reviewed contracts rather than an unsafe generic override field. Unsupported inputs fail closed now. Future support should be a separately versioned registered block that reuses the same canonical evaluator; never a parallel calculator.

## 5. GST distinction and exact arithmetic

- **Tariff** defines whether its stated unit price includes or excludes GST. It does not create exemption rights.
- **GST Profile** defines approved tax class, legally reviewed supply grouping, nonnegative approved rate heads, effective seller. No registered GST authority integration is claimed.
- **Component event** determines whether a fee is chargeable at all. Not triggered != exempt != zero rate != missing GST.
- **GST assignment** is Zone/channel specific; customer pricing Group membership never changes statutory tax treatment by itself.
- For each legal supply group, the evaluator calculates *unrounded exact* tax contributions, rounds the group tax total once to paise (half-up), distributes remainder to components and tax heads using deterministic largest remainder + stable order, and reconciles every line. Inclusive line **gross remains exactly equal to its stated price**. Exclusive line **net remains exactly equal to its stated price**.
- All results include integer-paise `net_minor`, `tax_minor`, each `tax_heads_minor`, `gross_minor`, source, estimated flag, price mode, classification and disclosure. Output must obey `gross=net+tax` both per-line and aggregate.
- Final mode refuses estimated required measurements. An upfront energy amount not yet delivered appears as a `NOT_YET_DELIVERED` *disclosure*, not a fabricated zero-energy final charge.
- A legitimate zero-priced component yields an explicit zero-priced line with its classification, not a missing component.

**Example**, hypothetical 18% total GST (9% CGST+9% SGST; *not* a certification of the real EV-service rate): charging 2 kWh x Rs118 inclusive -> Rs200 net + Rs36 included GST = Rs236 gross; visit Rs100 exclusive -> Rs100 net + Rs18 added GST = Rs118; gross Rs354, net Rs300, GST Rs54. Tests verify exact values and edge-case rounding.

**Legal boundary:** the project currently lacks verified statutory tax/invoicing integration. Displaying an operator-configured breakdown in a commercial statement does not create a legal GST tax invoice or prove legal correctness. Where law requires a tax invoice, that obligation must be met through an authorized compliant process. No IRN, invoice registration, e-waybill, GSTN verification or input credit entitlement is asserted.

## 6. Admin API surface (new, proposed executable routes)

All paths relative to `/api/v1/admin`. These handlers are registered in Django Ninja; the isolated PostgreSQL acceptance run verified commercial route registration. Swagger documentation parity is checked by `scripts/verify_commercial_swagger.py`.

| Method | Path | Action |
|---|---|---|
| GET/POST | `/commercial/tariffs` | List/draft reusable tariffs |
| GET | `/commercial/tariffs/{policy_id}` | Get tariff version |
| POST | `/commercial/tariffs/{policy_id}/publish` | Publish immutable version, `If-Match` |
| POST | `/commercial/tariffs/{policy_id}/duplicate` | Copy into independent draft |
| POST | `/commercial/tariffs/{policy_id}/duplicate-and-assign` | Copy, publish and assign atomically for eligible target |
| GET/POST | `/commercial/groups` | List/create pricing Groups |
| POST | `/commercial/groups/members/{customer_id}` | Change customer group, one active |
| GET/POST | `/commercial/gst-profiles` | List/create GST drafts |
| POST | `/commercial/gst-profiles/{profile_id}/approve` | Record authorized tax review |
| POST | `/commercial/gst-profiles/{profile_id}/duplicate` | Copy profile to independent draft (not approved) |
| GET/POST | `/commercial/tariff-assignments` | List/assign tariff to Zone or Group |
| POST | `/commercial/tariff-assignments/{id}/replace` | Atomic effective-default replacement |
| POST | `/commercial/tariff-assignments/{id}/close` | Remove target with coverage guard |
| GET/POST | `/commercial/gst-assignments` | List/assign approved GST to Zone/channel |
| POST | `/commercial/gst-assignments/{id}/replace` | Atomic GST replacement |
| POST | `/commercial/gst-assignments/{id}/close` | Remove GST target with coverage guard |
| GET | `/commercial/zones/{zone_id}/channels/{channel}/readiness` | Explain missing required configuration |
| POST | `/commercial/zones/{zone_id}/channels/{channel}` | Enable/pause new commercial eligibility |
| POST | `/commercial/preview` | Admin-only **nonbinding** scenario and full breakdown |

Mutations use the project's existing `Idempotency-Key` boundary, RBAC/deny handling, transaction and AuditEvent. No new Customer API or HAL surface is authorized here. Readiness does not prove real charger serviceability.

## 7. Fail-closed error and recovery contract

Errors have stable categorical codes: `ZONE_DEFAULT_REQUIRED`, `GST_COVERAGE_INVALID`, `GST_NOT_APPROVED`, `GST_SELLER_MISMATCH`, `GST_CLASS_MISSING`, `ASSIGNMENT_CONFLICT`, `INVALID_CHANNEL_BLOCK`, `UNVERIFIED_CONTEXT`, `UNVERIFIED_MEASUREMENT`, `MEASUREMENT_MISSING`, `TAX_RECONCILIATION_FAILED`, `MONEY_OVERFLOW`, etc. The Admin UI should link to assignment/profile/Zone correction instead of exposing a raw traceback.

All configuration writes must be authorized, atomic, audited, reversible when non-financial, and preserve any accepted or historical commitment. The HTTP layer must never accept a client-supplied `verified=true` as evidence. For a real quote, the separate trusted serviceability, duty/QR, route and tax-legal ports from Sprint8 still need to be implemented and accepted.

## 8. Existing-source compatibility and migration

- All migration files 0001-0009 are unchanged. New migration 0010 adds seven models and replacement PostgreSQL policy guard, plus overlap/readiness/immutability triggers. Legacy Zone tariffs/GST and their historical decisions are *not* auto-converted or deleted. Existing old Admin Zone endpoints remain available while the new configuration is explicitly staged.
- `CommercialTariffPayload` is attached 1:1 to the **existing** `PricingPolicy` publication ledger. It is not a second tariff lifecycle ledger.
- A Zone can be `PUBLISHED` while a new commercial channel is disabled; it becomes usable by this new commercial configuration only after explicit enabled/readiness validation.
- `CustomerProfile`/`Principal` remain the existing customer truth, with a separate effective-dated pricing Group association. No new customer credential system.
- No automatic migration of unverified GST legal decisions. Operators must explicitly review new profiles and assign them to channels.
- Old customer quote/accept endpoints remain `410`. A source install or DB migration alone does not create customer commercial authority.

## 9. What has been verified now

- `python -m pytest -q tests/test_commercial_machine.py tests/test_commercial_statement.py`: pure-domain acceptance tests verified, including exact totals, grouped rounding, old/new assignment boundary, Group-over-Zone precedence, Group cannot mask missing Zone default, service unavailable, GST missing/conflicting, tax types, distance slabs, waiting grace, final evidence gate, zero-priced rules, document settlement gate, deterministic repeatability.
- Python syntax compilation for new source files and migration.
- Installer preflight against baseline **should be run against a clean developer worktree**; installation is deliberately not performed in this environment.

**Verified on the isolated local PostgreSQL acceptance run:** Django checks, migration/model parity, migration 0010 forward/empty reverse/reapply, PostgreSQL catalog and durable guards, the commercial activation/last-default/GST immutability/overlap/parallel assignment/atomic replacement/preview/route checks, 51 existing integration tests passing (one skipped) and 29 unit tests passing. **Still pending:** persistent developer DB identity + migration, Swagger presentation audit, historical upgrade fixture, full customer serviceability/quote/settlement, legal GST review and physical HAL acceptance.
