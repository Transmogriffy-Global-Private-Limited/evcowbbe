# CoW Core V3.2 - Source-only correctness closure and frontend contract

**Dated:** 2026-11-18, Asia/Kolkata. **Status:** package/source-only, uninstalled until operator confirms. **Base:** verified installed Core V3.1 over `evcowbbe/anubhab-work` pinned HEAD `58a4144d8f8a6a26ed718028068029fda24ef97b`. **Runtime gates:** remain DISABLED. This text does not mean PostgreSQL, Django, HAL, charger, PSP, or generated OpenAPI were executed.

## Why V3.2 exists

Three V3.1 source defects were established, not merely inferred: (1) every duplicate customer-consent POST overwrote the existing one-use bearer hash; (2) custom trust providers could supply a zero booking safety buffer because the central `BookingAcceptanceProof` accepted zero; (3) both Customer and Admin tracking could label a recent but too-inaccurate GPS observation `LIVE`. These changes correct those semantics without replacing wallet/booking, HAL, driver or Admin architecture.

## Immutable business and release contracts

- A booking owns exactly one mandatory minimum charging purchase and may contain multiple sequential physical sessions; acceptance reserves fixed fees, minimum charging and strictly positive buffer **without posted debit**.
- Start subtracts other wallet holds, freezes tariff/GST, budgets cumulative Wh and subtracts verified prior physical Wh; a new start requires matching physical/duty consent and a positive buffer. An already-accepted Go HAL physical Wh cap is immutable.
- HAL owns actual physical start, meter/stop facts and may overshoot in the real world. Django still does not prove real stop merely because it posted a command.
- Existing settlement, refund, wallet, payment capture, migrations 0017-0019, public role routes, and optional Maps abstraction are not reworked in this package. Regulatory/financial owner sign-off remains required for cancellations, zero-energy service, minimum shortfall GST treatment, payment-provider refund and buffer calibration.
- All configured gates remain OFF by default. A disabled OTP gate blocks customer consent, handover resolution and proof consumption, not just initial OTP issuance.

## Customer consent: one logical operation per ticket

**Route:** `POST /api/v1/app/commercial/handover/{ticket_id}/customer-consent`. Django Ninja operation ID: `cowV3CustomerHandoverConsent`. Body must be strict empty object `{}`. Authenticated ACTIVE app principal must own the booking and ticket. OTP release and no-Maps mode must be active. Driver must have independently verified the one-use OTP against the matching still-active duty and active connector; the booking must still refer to that duty; the driver must have a fresh, definitively nearby device-reported GPS observation. This does NOT cryptographically attest hardware location.

- First success (`DRIVER_VERIFIED` -> `CONSENT_READY`): derive 256-bit token via HMAC-SHA256, domain `cow-consent-v2`, keyed by the existing configured HMAC secret over immutable ticket, booking, owner, driver, duty, connector and OTP-digest values; store **only SHA-256(token)**, the prior proof fields and original TTL. Return `handover_token`, `replayed=false`.
- Retry (`CONSENT_READY`): with the same valid owner, active duty, connector, fresh location and nonexpired ticket, recompute the token. If its hash matches the persisted hash, return the **same original token**, `replayed=true`, without extending expiry, changing arrival evidence, changing `verified_at`, saving the ticket, resetting OTP attempts or creating another start intent. The ticket row is locked under `transaction.atomic()` and the shared operational gate, so concurrent retries serialize.
- Legacy-random V3.1 ticket or rotated HMAC key: return HTTP 409 `HANDOVER_CONSENT_NOT_REPLAYABLE` without replacing the old digest; the original V3.1 token remains valid until its existing expiry if the customer still has it. No hidden force-reissue. Existing tickets have at most five-minute TTL.
- Expired/consumed ticket, invalid owner, duty switched/ended, suspended driver, disconnected connector, stale GPS or disabled OTP: reject without credential mutation. After the successful Start consumes the ticket, replay cannot recover the token. Reissuing requires an independently verified new OTP ticket when permitted.
- No idempotency header is introduced for consent: the owner+ticket pair already identifies the **one** state transition and the HMAC reconstruction avoids storing plaintext in the general-purpose response receipt table. No second idempotency or token table.

**Response `data` fields:** `booking_id: string`, `ticket_id: string`, `handover_token: string`, `expires_at: ISO 8601 string`, `state: "CONSENT_READY"`, `proof: "CUSTOMER_CONSENT_AFTER_DRIVER_OTP"`, `replayed: boolean` (new additive field; false on first issuance, true on a legitimate replay). Never log, display on an Admin dashboard, include in an analytics URL, or cache in a public store. Keep in the authenticated client only until Start/expiry.

Other relevant errors: `HANDOVER_OTP_DISABLED` 503, `BOOKING_LOCATION_MODE_DISABLED` 503, `HANDOVER_NOT_VERIFIED` 409, `HANDOVER_JOB_OR_DUTY_CHANGED` 409, `LOCATION_DRIVER_STALE_OR_INACCURATE` 409, `LOCATION_ARRIVAL_NOT_PROVEN` 409, `HANDOVER_CONSENT_CONTEXT_INVALID` 409. Actual Django-generated OpenAPI must be validated later.

## Positive buffer at every new physical-admission boundary

- `BookingAcceptanceProof.check` refuses a zero buffer from ANY configured trust-provider, not merely the default device-GPS provider. Error: HTTP 409 `BOOKING_BUFFER_NOT_APPROVED` for zero. Non-integer, negative and overflow values remain rejected by the existing bounded `money` validator.
- `prepare_and_stage_start` checks the stored booking buffer is positive **before** consuming the handover proof or allocating a HAL permit; dormant zero-buffer bookings must not gain new physical authorization after a config/provider change.
- HAL `_permit_contract` independently checks the booking's buffer positive and matching the immutable financial permit's `commercial_snapshot.buffer_paise`, before durable command dispatch.
- The pure budget utility remains useful with zero for inert analysis. Historical already-performed service may still STOP, be reconciled and settled under the existing financial policies; this guard does not rewrite historical invoices or tax evidence.

## Customer and Admin driver tracking: truthful LIVE

**Customer:** `GET /api/v1/app/jobs/{job_id}/driver-tracking`, ID `cowV3CustomerDriverTracking`, owner-only. **Admin:** `GET /api/v1/admin/jobs/{job_id}/driver-tracking`, ID `cowV3AdminDriverTracking`, requires effective `dispatch.read` (including explicit DENY behavior). Both now use the same `latest_driver` and `is_fresh_driver_sample` classifier.

- `LIVE`: current assigned eligible active driver/duty, location receipt enabled, latest persisted sample within allowed age, no more than 10 seconds in the future, and declared accuracy at or better than `COW_LOCATION_MAX_ACCURACY_M`.
- `STALE`: an observation exists for the currently active driver/job but fails any accuracy/clock freshness criterion. Its timestamp and reported accuracy must remain visible so clients do not mistake it for real-time navigation.
- `NO_SIGNAL`: no observation, disabled location ingress capability, no current assigned live/eligible driver/duty, or a revoked/suspended driver. Do not emit retained coordinates in this case.
- `NOT_ACTIVE`: job has reached a nontrackable state. Do not leak terminal-job coordinates.
- This remains direct phone-supplied GPS, **not** hardware attestation, road navigation, traffic ETA, Map API dependency or commissioned WebSocket.

## Source integration and verification register

| Surface | Exact change | Downstream effect |
|---|---|---|
| `cow/services/booking_trust_nomap.py` | HMAC-domain consent token, pure replay, current duty/connector checks, OTP gate throughout | Stable customer Start token; old token remains unrotated; fail-closed duties |
| `cow/common/nomap_wire.py` | Add `ConsentView.replayed: bool = False` | Typed additive Ninja/Pydantic response; FE client can distinguish retry |
| `cow/services/booking_commercial.py` | Buffer on proof + stored booking Start | All trust providers and all new physical starts consistent |
| `cow/hal/v1_charging.py` | Positive buffer + permit snapshot equality | No bypass through separately staged previously committed permit |
| `cow/services/nomap_location.py` | Central driver validity and accuracy/time classifier | Arrival and user tracking agree on LIVE qualification |
| `cow/admin/nomap_routes.py` | Reuse shared freshness classifier | Admin no longer reports inaccurate GPS as LIVE |
| `tests/test_cow_v32_source_contract_static.py` | Source assertions for affected boundaries | Future deterministic static guard; no app runtime execution |

**No Django migration/model field, Go source change, new gate or API path is introduced.** The old `ConsentView` fields remain present, with one additive boolean. No generated OpenAPI file is asserted until an authorized actual Django schema generation. Existing server-side API route registration is not modified.

## Mandatory deferred tests / acceptance owners

1. Django imports + `manage.py check`, migrations 0017-0019 graph parity and disposable PostgreSQL upgrade from migration 0016; no migration 0020.
2. Concurrent consent POSTs, dropped response retry, digest/no-write invariant, consumed ticket, expired ticket, restarted OTP, HMAC rotation, legacy V3.1 token and replay without current duty.
3. Custom provider with 0 buffer, dormant booked 0 buffer at Start, mismatched HAL permit buffer, positive buffer with multiple concurrent holds, STOP and settlement after already-started zero-buffer historical job.
4. Driver/admin/customer actual HTTP auth, explicit Admin DENY, inaccurate/future/stale GPS, duty suspension and feature toggle, terminal privacy; verify generated `/openapi.yaml` and customer-junior generated SDK.
5. Payment capture, wallet concurrency, posted settlement and refund race schedules in real disposable PostgreSQL; Go persisted local-stop/network-loss and charger overshoot acceptance only after separate authorization.
6. Commercial/legal/finance sign-off on buffer calibration, zero/partial energy, fault visits, refunds, GST and invoice obligations before enabling charging or posting gates.

## Installation and Git boundary

Use only the independently generated V3.2 package's `--self-check`, `--check`, `--apply`, `--verify`, `--rollback` on the actual **already-installed V3.1** Windows checkout. Its manifest requires the exact old V3.1 bytes, tracked/untracked inventory, branch and HEAD. **Never reinstall V1/V2/V3/V3.1**, and never override user modifications or silently delete a source record. `--rollback` is permitted only while all touched files match recorded old or new hashes and saved backups verify.

After successful actual checkout application and review, stage explicit approved paths, inspect index/staged diff, then `git commit` and `git push origin anubhab-work` (not `main` or junior branch). Since V3.1 itself is still uncommitted, that future commit must include the intended V1-V3.1 new/modified files plus V3.2 deltas after reconciliation, not just the new V3.2 files. The synthetic archived sources must not be published as a guessed full current repository. Never force-push or claim pushed without verifying remote SHA. Runtime deployment and schema migration remain separately authorized.
