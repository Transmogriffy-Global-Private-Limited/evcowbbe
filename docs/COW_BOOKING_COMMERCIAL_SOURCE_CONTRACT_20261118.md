# Charger on Wheels - One-Booking Commercial Charging, Source Completion Candidate

**Date:** 2026-11-18 (Asia/Kolkata). **Baseline:** Git HEAD `58a4144d8f8a6a26ed718028068029fda24ef97b`, branch `anubhab-work`, with the already-installed 19 HAL review Git changes. **Go HAL:** pinned `1908c934c0e2570b81523e8ef3fe20be1e519da0`, **unchanged**. **Status:** SOURCE ONLY; NOT TESTED, NOT MIGRATED, NOT RELEASED. No claimed production acceptance.

## Authority separation

- `cow.domain.commercial_machine.evaluate()` is the **sole authoritative tariff/GST evaluator**. This package wraps it in trusted DB policy-version selection and immutable snapshots; it never substitutes its own charging rate table or tax formula.
- `CoWMinimumChargingSchedule` is a version-attached schedule of **gross INR paise minimum charging purchases** by approved travel-distance slab. It is not a transportation rate. The only writer restricts addition to a **DRAFT roaming published-policy revision** with audit and independent approval reference; existing published versions are not retroactively modified. Historical policies without a schedule cannot accept new CoW bookings.
- `CoWBookingCommercialCommitment` owns a **single** one-time booking minimum, fixed fees, buffer and wallet hold for its `ServiceJob`. `CoWBookingChargingAllocation` owns each subsequent, separately proven physical Go HAL start. `CoWBookingFinancialSettlement` owns the one posted booking debit. No permit-level minimum repetition.
- An independently authorized, server-configured trust provider must supply **durable, owner-bound, replay-resistant** route/location, acceptance, driver duty, OTP/consent and completion evidence. The provider port is **not** the proof provider implementation. Client-provided `verified: true` is never admitted. An approved adapter must be implemented, reviewed and enabled separately.

## Exact pricing and cap contracts

All amounts below are **gross INR paise** including applicable GST; `T(Q)` denotes the frozen canonical evaluator's gross charging price for cumulative **integer Wh** `Q`. No binary floats or inferred power-to-energy conversion. `W` is a row-locked **posted** wallet balance, `R` the sum of all **other** CoW job holds, `F` frozen mandatory transportation/visit/default fees, `M` distance-slab minimum charging purchase, and `B` the protected buffer. The active booking's own hold is excluded once when deriving `R`. The buffer is not expendable ordinary charging money.

- Booking eligibility: `W >= R + F + M + B`. Acceptance holds `F + M + B` and posts **no debit**.
- At customer Start, after trusted driver arrival, location proof, consumed job-bound OTP and customer consent, **recheck under the wallet lock**: `extra = W-R-F-M-B >= 0`, `charging_budget=M+extra=W-R-F-B`. Expand the existing booking hold to preserve authorized exposure and buffer. No double subtraction of M.
- Calculate `mandatory_wh=max{Q: T(Q) <= M}` and `total_wh=max{Q: T(Q) <= charging_budget}` with the **same** canonical frozen evaluator and tax snapshot. `extra_wh=total_wh-mandatory_wh`. This decomposition is for disclosure; **never independently sum inversions for nonlinear price schemes**. This candidate **only supports validated `ENERGY_KWH` tariffs with strictly positive rate**, and rejects time/session, waiting-time, unsupported distance changes, price inconsistency, missing GST and ambiguous policy selection.
- New per-physical-session limit: `min(total_wh - already_verified_completed_wh, per_session_wh_ceiling)`; reject if nonpositive. The total is also bounded by the customer-requested energy ceiling and provider-approved equipment ceiling. Every previous started physical transaction must have one exact, applied, non-contradictory `transaction.completed` fact and known metering before another physical start. A start/stop/delivery uncertainty blocks additional start.
- Physical command is staged only at the real **Start** click, not booking acceptance. The existing Go HAL receives persisted `limit_type='AUTO'`, `energy_limit_wh` and `energy_limit_source='WALLET'` for **that physical session**, and independently enforces its cumulative OCPP energy limit. After a charge stop, another session may be staged **under the same job** with a new verified handover/consent and **only residual** energy permission. A verified top-up may increase that next cap, never mutate an already-sent cap.

### Acceptance examples (illustrative, not actual tariffs)

Rate gross ₹20/kWh, F=₹150, M=₹200, B=₹50, R=₹0, user requested energy >=15kWh.

| Wallet | Eligibility | Extra | Total authorized first-session Wh, before session-specific cap |
|---|---|---|---|
| ₹350 | REFUSED (B missing) | - | none |
| ₹400 | ACCEPTED | ₹0 | 10,000 Wh |
| ₹500 | ACCEPTED | ₹100 | 15,000 Wh |
| ₹500, prior 6,000 Wh completed | ACCEPTED | ₹100 | 9,000 Wh residual |

The Wh outputs require actual tariff/GST inversion and may differ by paise rounding or published policy. All money is exact integer paise.

## Final posted debit and non-negative wallet

- At accepted booking completion, after a trusted driver-completion verifier and all physical completion evidence are present: `total=F+max(M,T(total_verified_completed_wh))`. Tax breakdown from approved frozen GST, with a distinct labeled minimum-purchase shortfall. A zero-energy completion **does not automatically prove a customer-earned minimum**: the trusted completion verifier must prove this is chargeable under approved terms; cancellation/operator-fault paths are not auto-billed.
- One wallet row lock, one `WalletTransaction` (stable `cow:booking:settle:{job id}` reference), one `CoWBookingFinancialSettlement` per booking. No debit on meter fact or booking acceptance, and no duplicate debit on replay.
- During overshoot, wrong meter identity, ambiguous HAL command, insufficient wallet or missing completion, retain truth and quarantine to `RECONCILIATION_REQUIRED`. Never fake a charger stop, take funds from buffer without approval or let the posted wallet go negative. An immutable identical shortfall settlement can recover after a verified wallet top-up and explicit approved settlement call; it cannot reprice or silently forgive overshoot.
- A durable wallet hold means other CoW booking admissions subtract that committed balance under a wallet row lock; the existing wallet's `balance_paise` remains posted, while its projection shows held and available separately. **All future wallet debit integrations must respect these holds; source-level integration is not an application/runtime proof.**
- A charging stop is a durable command and charger behavior, not a synchronous guarantee. Physical overshoot may occur due to meter-reporting lag, disconnected charger, uncertain RemoteStop, charger firmware or stop refusal. Buffer policy/acceptance testing is a release prerequisite; `Go HAL ↔ CMS` independence is only guaranteed for a **previously delivered, stored Go HAL limit** while charger ↔ Go HAL communication and the enforcement loop function.

## Safety / Idempotency / recovery

- No installed source in this package executes automatically. It does not add a running scheduler or issue any HAL request on installation.
- Booking acceptance protected by `ServiceRequest` and unique `(owner, booking_key)` + `ServiceJob` one-to-one; replay compares frozen proof scope.
- Stage Start protected by `(booking, start_key)` and each permit's single Start intent. Provider must prove consumed OTP/customer consent was single-use and correct job. `send_once` marks a durable attempt before any network call and **rechecks booking hold/state before transmission**; no blind attempt retry on network uncertainty.
- Late fact, conflicting OCPP stop evidence or overshoot halts further starts and posting rather than losing evidence. `recover_expired_unattempted_start` requires proven never-dispatched state before releasing a permit reservation; any command with `attempted_at` stays held for human reconciliation.
- An operator-controlled settlement command defaults to **dry-run**; `--execute` requires separate commercial and wallet-posting gates and an independent trusted completion proof. Its completion-ref provider is not supplied by this package.
- `0018_cow_booking_commercial.py` depends on unexecuted `0017_hal_v1_charging_source.py`; migration files exist but neither was run. Only after disposable DB and lifecycle acceptance should staging/production migration be considered.

## Explicit unresolved release prerequisites (blocking)

1. Implement production server-side trusted route/location/OTP/consent/driver-completion provider, prove nonce replay control, GPS accuracy/staleness/geofence/duty ownership, rider and driver authorization and service-charge earning/cancellation decision.
2. Approve and publish real minimum charging schedules as new policy versions; ensure prices, GST and minimum-purchase shortfall legal treatment are approved by authorized finance/tax owners. No numbers invented here.
3. Prove wallet hold invariants across **every** posted debit path, refund, top-up, admin adjustment and concurrent booking (including global lock ordering); currently the source inspects existing recharge mutation and adds a booking-specific hold, not a complete external payments certification.
4. Source design and implementation have **not** been Django-import/migration-checked, DB-tested, HTTP-integration-tested, Go-backed, charger-tested, performance-tested, deployed or enabled. Disable `COW_BOOKING_COMMERCIAL_*`, `COW_HAL_V1_NEW_STARTS_ENABLED`, and `COW_HAL_V1_FINANCIAL_POSTING_ENABLED` until independently verified.
5. Final operational processes must cover disputed/failed visits, premature driver closure, pre-booking cancellation, overdue/unpaid claims, OCPP overshoot, invoice issuance, support, adverse provider outages and admin approval; this source does not invent legal defaults.
6. A product-level energy ceiling must agree with customer-requested Wh. A later customer-approved extension to the **same** booking must have a new authorized amendment workflow and cannot be inferred from a wallet recharge.

## Operational source-only handoff

Use the guarded archive installer `--check`, `--apply`, `--verify`, `--rollback` on the specified Git HEAD and exact previously installed 7-modified/12-untracked source snapshot. Keep its install state and backups in the extracted ZIP folder. `--rollback` should only be used when files remain at recognized original/reviewed bytes. Any unknown Git change, mismatch, generated file, altered package or staged edit fails closed. No Django management command needs running for this source-only installation.
