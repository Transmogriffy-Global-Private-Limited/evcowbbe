# CoW V3.5: Commercial OpenAPI operation family correction

## Proven incident

The real disposable PostgreSQL 17.5 schema run on 2026-11-19 confirmed migrations 0001..0020, schema identity, `0020` no-DDL, and 14 new CoW tables, but failed to generate the API. The published `cow/fe_openapi.py` rejected every `/api/v1/admin/commercial/*` operation whose operation ID did not begin with `adminCommercial`, while `cow/admin/nomap_routes.py` had added three valid Admin V3 finance routes with `cowV3Admin...` operation IDs. The first generated failure was `cowV3AdminApproveBookingCompletion`; refund request and refund approval would have failed next. This is an FE contract decoration failure, not a PostgreSQL migration error. The existing database acceptance remains open.

## Change contract

- Keep published `0017`..`0020` immutable. Do not change views, financial state transitions, permissions, feature flag defaults, endpoint names/IDs, or migration state.
- Route identities in `cow/admin/nomap_routes.py` remain `cowV3AdminApproveBookingCompletion`, `cowV3AdminRequestWalletRefund`, `cowV3AdminApproveWalletRefund` for the three exact, POST-only paths.
- Treat these as Admin financial business operations, not as tariff/GST configuration; present them under their own clearly labeled OpenAPI tag. Require Admin bearer security, declared typed success envelopes, strict JSON body and `Idempotency-Key`; annotate only verified semantics, including release gates and distinct wallet vs PSP obligations.
- Preserve existing `_enrich_commercial_operation` behavior for all 24 legacy commercial configuration routes. Unknown V3 or unrelated operations under the prefix must continue to fail closed.
- Run the **actual Django OpenAPI generator with a database-connection guard**, using a sanitized, test-only environment and loopback-only dummy PostgreSQL URL, before any more PostgreSQL cluster work. Verify operation inventory, operation IDs, auth, typed successes, requested idempotency, and YAML/JSON parity. A successful OpenAPI run proves documentation generation, not API business runtime or public hosting.
- Source overlay is exactly one tracked modification and two new files on the pinned, unchanged `anubhab-work` HEAD `7c589141043c883657ba481481be9191202471c0`. No Git writes, migrations, or real HAL/financial traffic by this package.

## Acceptance and handoff

A ZIP installer validates the parent Git blob and clean worktree, applies the three files only, and can roll back exactly those bytes. Independent `unittest` module and a genuine Django offline OpenAPI probe are required before the corrected **isolated** PostgreSQL runner may be invoked. That runner must explicitly allow only the three exact overlay paths and should continue to enforce loopback/data-directory identity and flags OFF. After successful isolated schema + tests, review and publish the minimal source correction to `anubhab-work` with a new pinned HEAD; do not merge to `main` or deploy without separate authorization.
