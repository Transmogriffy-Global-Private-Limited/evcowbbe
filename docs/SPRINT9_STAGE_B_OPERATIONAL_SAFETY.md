# EVCOWBBE Sprint 9 Stage B: shared operational admission

## Basis and scope

- Exact source target: `main` and `anubhab-work` commit `d16686484c3df9e1d7bb305c232a27a5144c2eef` (the saved-resource-count correction).
- September 24 blueprint: canon duty states `STARTING, AVAILABLE, OFFERED, ASSIGNED, EN_ROUTE, AT_CUSTOMER, SERVICING, BREAK, UNAVAILABLE, ENDING, ENDED, ABORTED`, not the legacy model's `OFF_DUTY` default; Stage B requires `SERVICING` for a charging start.
- V1 DriverProfile active eligibility value matches the existing repository's `eligibility="ACTIVE"` test fixture; a Driver's `Principal.status=ACTIVE` is separately required. Verification-email flag and licence expiration are independently tested.
- The Stage B eligibility and fleet guards are **operational only**. They do not implement binding customer quotes, booking/payment authority, physical HAL telemetry truth, Driver duty start/end APIs or Driver approval. **Not a commissioning permission.** HAL worker remains off by default and must stay off until separate trusted commercial/charging release gates are satisfied.
- No new API route, table, model or migration. Existing Admin endpoint paths, operation IDs and response schema are preserved. More requests correctly reject with `409` for newly enforced safety invariants.

## Source owners and cross-code dominoes

| File | Change | Safety consequence |
| --- | --- | --- |
| `cow/services/operational_eligibility.py` | New shared gate and eligibility decision | One place for typed operational safety checks; no Admin-only or Driver-only duplicates |
| `cow/services/fleet.py` | Twelve Admin mutations recheck fresh RBAC and share the gate | Suspended/revoked Admin cannot race a fleet write; critical asset/pairing/maintenance changes cannot strand a current duty, active physical session or unresolved command |
| `cow/admin/jobs.py` | Charging-start uses shared admission and typed 202 receipt | Active `SERVICING` duty; eligible verified Driver; licence/certificates; effective pairing; registered active connector; no competing jobs, maintenance, sessions or commands |
| `cow/hal/client.py` | Pre-send safety admission under gate | A stale *PENDING/not-yet-delivered* StartCharging command is rejected and audited without network send; `DELIVERY_ATTEMPTED`, `HAL_ACCEPTED`, `OUTCOME_UNKNOWN` retain strict reconciliation semantics |
| `cow/fe_openapi.py` | Existing charging-start operation documents required idempotency, new 409 blockers and operational-only admission | API contract stays accurate without adding a new route |
| `cow/hal/ingress.py` | Signed facts apply under the gate | Physical evidence commits in a transaction serialized with relevant operational changes; inbound fact truth is not discarded merely because a new command would fail now |
| `tests/integration/test_operational_eligibility_pg.py` | Isolated PostgreSQL tests | Duplicate command denial, negative eligibility, pending command no-send, concurrent command uniqueness, fleet interlocks, revoked/ended duty |

## Invariants and lock ordering

1. Admin mutating request passes initial bearer/RBAC; **inside** the mutation transaction, obtain `admin_rbac._lock_global`, validate a fresh active verified Admin and capability, then acquire Stage B operational advisory gate.
2. Non-Admin HAL worker/fact paths acquire the operational gate first, followed by their own command/event/job/asset locks. External HAL HTTP happens **after** the `DELIVERY_ATTEMPTED` intent commits.
3. This intentionally coarse transaction advisory lock serializes all participating safety-critical operations while Driver duty creation/suspension lacks a published API. This is a safe temporary design, not optimal high-throughput architecture. Profile and job writes made outside these paths or via direct SQL are not protected: **future Driver duty, dispatch, incident, manual correction, and job-transition services must acquire the same gate before those writes become operational.**
4. Start command requires the job in `SERVICE_PREPARING`, assigned open duty in `SERVICING`, verified active Driver Principal, `DriverProfile.eligibility=ACTIVE`, valid licence date in Asia/Kolkata, active AVAILABLE vehicle/charger, vehicle valid registration+insurance+inspection, known HAL identity, valid active connector and pairing, no overlapping maintenance, no other in-progress job on duty, no physical session on job/connector, no unresolved StartCharging command for job.
5. Routine Admin fleet operations reject if relevant open claims, active charging sessions, or unresolved HAL commands could be stranded. This is **not** the emergency removal path: that path needs dedicated incident/action/reconciliation evidence and cannot be simulated with a status toggle.
6. Once `DELIVERY_ATTEMPTED`, do **not** send a replacement command merely because HTTP returned uncertainty. HAL facts are recorded independently with exact event IDs.
7. Quiescence is checked inside DB transaction; in this release all **participating** writers serialize. Database triggers currently do not cover every cross-resource invariant for uncoordinated raw writes. No claims of universal SQL-level enforcement.
8. There is no implicit device telemetry or verified commercial commitment in these checks. Admin preview remains simulation-only.

## Safety blockers and intended operator behavior

- `DUTY_NOT_ACTIVE`, `DRIVER_NOT_ELIGIBLE`, `ASSET_NOT_OPERATIONAL`, `ASSET_IN_MAINTENANCE`, `ASSET_PAIRING_MISMATCH_BLOCKED`, `CHARGER_HAL_IDENTITY_UNKNOWN`, `CONNECTOR_NOT_OPERATIONAL`, `CHARGING_COMMAND_CONFLICT`, `DUTY_HAS_ACTIVE_JOB`, `CHARGING_SESSION_CONFLICT`, `CONNECTOR_IN_USE`, `ASSET_OPERATION_BLOCKED` are machine codes in the standard `{error:{code,message,request_id,details}}` envelope.
- A blocked fleet edit is 409, not a silent forced state transition. Use explicit incident/reconciliation workflow in future release.
- `HALCommand` rejected before its first network send is marked `REJECTED` with internal `COMMAND_PRESEND_REJECTED` AuditEvent recording a **code**, not secrets. Unexpected network delivery outcomes remain `OUTCOME_UNKNOWN` pending explicit reconciliation.
- The Admin `POST /jobs/{job_id}/charging/start` retains its existing 202 response contract, but **a 202 means command intent queued only**, never physical charging, commercial authority or customer collection.
- Existing `GET /vehicles`, `/chargers`, `/pairings` still present inventory projection, not trusted dispatch or live HAL readiness.

## Verification and release gate

1. No untracked pre-existing modifications; `git status` must be clean before applying the package. No source/branch writes except intended local edits.
2. Package's `tools/selftest.py` must pass synthetic LF/CRLF, anchor failure and rollback scenarios.
3. Run Django `check`, pure/source tests, `makemigrations cow --check --dry-run` against the offline environment; never use the configured Aiven default for acceptance.
4. Run `tools/verify_isolated_pg.ps1` in a separate PowerShell process to create a **new local PostgreSQL 17 cluster**, apply/reverse/reapply schema 0008/0009/0010 and run all existing+new integration regressions. Verify shutdown and no persistent dev DB effects.
5. Check `git diff --check`, exactly six previously tracked files changed plus four new files (one shared module, two tests, one document), and inspect every diff. No new migration.
6. Do not commit/push/rehost if any gate fails. Only after green, review user-side contract delta, review exact remote main SHA and promote with a controlled Git procedure. Local `rehost-evcowbbedev` is a separate action.
7. Future Stage C Driver workforce services must use the `fresh_admin/lock_gate` ordering, and no Customer-owned files are to be edited by Stage B.

## Known unclosed downstream gates

- No full Driver onboarding, duty creation/ending, suspension/offboard API and verified schedule/location eligibility. A hand-created DutySession is test-only evidence; Stage B does not make one operationally authoritative.
- No full Zone-to-quote-to-settlement binding workflow, HAL physical commissioning/compatibility test, charged amount/wallet ledger or approved invoices.
- No standardized Admin CORS/Swagger response for all new safety errors; introspection should be exercised with live Django during acceptance. No new endpoints added.
- Global gate is intentionally a throughput bottleneck. It can be split into canonical resource-specific sorted locks **only after** every writer is inventoried and concurrent integration tests run.
- Existing `scripts/db_isolated_pg.ps1` may emit an environment restoration warning internal to its child process. It does not change the interactive parent PowerShell environment, but remains test-harness technical debt.
