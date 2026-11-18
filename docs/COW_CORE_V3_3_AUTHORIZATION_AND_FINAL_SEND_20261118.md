# CoW Core V3.3 - Admin replay authorization and HAL final-send integrity

2026-11-18 (IST). Parent: `58382d083144d700cdd0141fd0903920b348d248` on `anubhab-work` only.

## Source facts, decisions, and states

- Existing `idempotency.execute()` performs a cached-receipt lookup before invoking the business callback. Existing Admin business callbacks do fresh RBAC checks, but those callbacks do not run for a cached result. Therefore the API wrapper now enters `transaction.atomic()`, acquires the global RBAC lock and locks/rechecks the current Admin actor through `safety.fresh_admin` **before** receipt lookup. Completion approval additionally rechecks `payments.manage`. Each route checks its current independent release flag before allowing a cached reply. The original idempotency receipt, deduplication, and business operations remain unchanged.
- Existing source writer lock order is global RBAC -> operational gate -> business rows. New Admin replay path is global RBAC -> principal -> per-receipt advisory -> operational gate -> business rows; there is no new RBAC lock after receipt acquisition. Auth and gate errors cause no business callback or receipt read.
- `HALV1ChargingCommand` START remains `PERSISTED` until the worker marks `DELIVERY_ATTEMPTED` durably. At that last pre-network transaction, use `_permit_contract` with `BOUND` permit, not staging `RESERVED`; this checks current owner and booking/permit status, cryptographic booking snapshot digest, positive matching buffer, tariff/GST snapshot identity, original authorization Wh and budget, existing wallet holds and current balance. STOP continues through the existing independent branch.
- Frozen outbound START snapshot must match exactly all approved fields: trace/command/intent/customer/charger/connector/scope IDs, OCPP identity/connector number, credential/command expiries, and all Wh/time limit types/sources/values. Strict types and exact key set prevent injected or silently updated wire authority. Recheck current operational admission and booking new-start gate, plus the existing mapping check, while under the shared operational gate. Never automatically mint a replacement command or replay an ambiguous network attempt.
- If a staged START fails a new pre-send proof, move command, intent, and permit to `RECONCILIATION_REQUIRED` **without network I/O**. Existing expired-permit handling still yields `EXPIRED`. Retain financial obligations; do not silently release a held booking. STOP/reconcile and historical sessions remain supported.
- After durable `DELIVERY_ATTEMPTED` commit, the Go request is sent outside DB locks, as before. An independent external write between that commit and actual network send remains a bounded race inherent to out-of-transaction transport. It must be covered by DB/state guards and later real integration tests, not misrepresented as impossible.

## Verification and limitations

- Source-only AST and hermetic behavioural tests verify denied cached replies when Admin role or secondary capability is lost or feature gate is disabled; authorized replay is unchanged. They exercise the actual pure frozen-command predicate over all authorized fields, numeric type/extra key drift, and source flow ordering.
- Python syntax validation covers changed Python files; Git blobs and expected base commit are pinned. No Django import, PostgreSQL migration, real RBAC race, OpenAPI generation, Go network call, OCPP charger test, wallet posting or deployment is claimed by these source tests.
- Existing migrations 0017, 0018 and 0019 and configured release-gate defaults remain unchanged. One source-only commit to `anubhab-work`; do not merge/push to `main`.

## Next mandatory acceptance gate

Disposable PostgreSQL/Django integration: revoked/explicit-DENY Admin replay under concurrent capability changes; release gate flips; duplicate Idempotency-Key and request digest mismatch; booking tariff/GST/buffer/Wh/hold corruption; duty revocation before dispatch; frozen snapshot drift; queue retries after reconciliation; STOP during disabled START; actual generated OpenAPI, Django migrations and command/receipt path. Only authorize real HAL and charger tests separately.
