# CoW Django HAL v1 source-completion specification

Date: 2026-11-18. Source-only work. Neither charger nor local PostgreSQL tests nor network integration requested in this phase.

## Exact baseline and ownership

- CoW Django `Transmogriffy-Global-Private-Limited/evcowbbe` `58a4144d8f8a6a26ed718028068029fda24ef97b`.
- Unchanged Go HAL `Transmogriffy-Global-Private-Limited/ocpp-hal-go-new` `1908c934c0e2570b81523e8ef3fe20be1e519da0`.
- Django owns CPO/customer authorization, fleet mapping, start intent, session projection, affordability/tariff/tax, wallet holds, settlement, and customer-visible history. Go owns OCPP, protocol dispatch, exact HAL/OCPP transaction IDs, raw meter/SoC, outbox and fact delivery. Two independent databases and credentials.
- No Go changes; no writes to Aiven or VPS; no real/virtual charger, runtime tests, or claims of runtime acceptance.

## Confirmed existing/remaining scope

Existing source: Django M1 mapping and Admin machine operations, authenticated fact/trace receipts, runtime status, additive-mapping guard, and network client. Go supplies mapping, START/STOP, exact lookups, charger operations, facts, trace delivery.

Remaining Django source work: create/validate commercial authorization port; stage/dispatch/reconcile start and stop commands; use one-use credential; maintain start intent and session state; accept/project command, transaction.start/meter/soc/completed; exact customer reads; finance and duty safety integration; operational process/recovery; schema and migration; contract documentation.

**Observed contract mismatch**: Go `v1_lifecycle_postgres.go` omits `cpo_id` from `command.updated`, `transaction.meter`, `transaction.soc`, `transaction.completed`; for command updates even `cms_charger_id` is absent. Django M1 receiver `_mapping` assumes both exist in every fact. Receiver must resolve authenticated fact ownership from authoritative stored command or start-intent, plus exact charger/OCPP identities. Never fabricate scope or accept an uncorrelated fact.

## Frozen safety architecture

1. Start has no direct customer-controlled payload for CPO, money, tariff, charger identities, credential, or limit conversion; it references an internally issued, committed commercial authorization/hold. Only an approved backend business writer can create that authorization. Source must not infer an approval from a `PREVIEW` pricing quote, an unverified address, or an unheld wallet balance.
2. Two front doors ROAMING and FIXED_QR use one HAL state machine, but channel-specific admission and trustworthy location/assignment are outside the HAL's authority. Without those verified upstream grants, both must remain unavailable; a feature flag defaults OFF.
3. Persist start intent, encrypted credential, command identity, exact mapping snapshot, commercial proof and hold reference before any HAL network call. No network I/O in a database transaction. Intent exists before any active session.
4. The Go `id_tag` must start `appv1_`, at most 20 ASCII bytes, be one-use, be private, and not be a customer UUID. Encrypt at rest using an independently derived encryption key; never echo via API/logs. Expiry of credential strictly earlier than command expiry.
5. After a send attempt is durably marked, never silently retransmit a new logical command on timeout or process restart. Reconcile exact same CMS command/intent; `404` alone cannot prove physical non-execution. All state changes monotonic or evidence-backed, with an explicit reconciliation state.
6. Exact transaction.started evidence and preexisting intent+command+credential+connector mapping may create ACTIVE session. RemoteStart accepted does NOT create session. RemoteStop accepted does NOT complete session. A final completion fact establishes *physical completion*, not settlement or release of funds.
7. Meter Wh integer and sequence monotonic; SoC independent sequence and bounded canonical decimal; no extrapolation. Completion must match exact OCPP/HAL transaction and validated, nondecreasing meter truth. Late/out-of-order facts remain immutable evidence, safe PENDING/RECONCILIATION processing rather than guessed success.
8. Preserve the accepted HAL receipt immutability trigger and transaction processing-state-only updates. Diagnostic traces never establish charging or finance truth.
9. Custodial stop, reconciliation, fact replay and refund/settlement recovery remain available when new-start admission is disabled; no permission bypass by placing customer token on HAL HTTP.
10. Operational lock ordering: existing admission advisory transaction gate precedes charger/connector/intent/session locks; short database transactions, network calls outside. Stop must never be blocked by a suspended customer if session ownership is already proven.
11. New migrations append `0017` only; never edit applied `0016`, whose DEV ledger is already accepted. Database tests and migration parity deliberately not executed in this phase.
12. Default new-start flag OFF, no runtime deployment or public activation in this drop. Code must distinguish completed software source from unverified runtime and unsatisfied commercial/financial dependencies.

## Non-negotiable external dependency, not an unspecified assumption

The current customer quote/acceptance API explicitly returns LEGACY_QUOTE_RETIRED and the existing wallet has no production-accepted charging hold/settlement service. The HAL module may implement a committed approval interface, but cannot manufacture real tariff/tax truth, trustworthy QR location, wallet reserve, or settlement. Upstream modules must supply auditable authorization and exact financial ledger before a production start is permitted. Existing HAL code must not write fake billing or auto-release holds based solely on a command ACK.

## Acceptance after source freeze (intentionally NOT executed now)

- Model/migration byte parity; PostgreSQL 17 scratch schema and trigger checks; no production migration.
- Go request/response shape parity for every v1 endpoint and exact fact emitter payload (including omitted CPO fields).
- Durable state and concurrency test matrix: start once, lost responses, delayed start fact, late completion, duplicate digest, foreign scope, mapping drift, stopped physical session, meter/SoC ordering, multi-connector, stop despite suspended customer, stale locks, crash/restart, no double finance event.
- Local authenticated HTTP client/server; virtual charger later by explicit owner choice; actual charger much later.

## Status semantics

"Source implemented" means files exist and have been statically reviewed. "Tested" requires executable evidence. "Integrated" requires live Django+dedicated Go plus exact DEV identity isolation. "Commissioned" requires safe charger-specific validation. None can be inferred from the prior one.


## Consolidated post-install source review (2026-11-18)

Static code review identified and corrected the following cross-layer defects
without running migrations, Django, a server or charger:

1. Credential encryption uses a new `hal-v1-one-use-credential` derivation purpose,
   not the email/encrypted-outbox Fernet domain. Existing unexecuted migration 0017
   remains additive; no persisted charging credential has been created by this package.
2. A stop is staged using the durable, frozen start-intent charger/connector
   identity, never a currently remapped physical gun. A missing/corrupt frozen
   snapshot is refused. A changed HAL mapping can still reject dispatch, requiring
   operations reconciliation: this is not a guarantee of physical stopping.
3. Failure to decode a stored start credential refuses safely *before* the
   durable send-attempt marker. Once marked attempted, no automatic re-submit.
   Malformed post-network response is safely recorded as uncertain.
4. A late OCPP acceptance cannot silently erase an existing intent
   reconciliation hold, and charger start evidence outside the commercial
   permit window requires reconciliation.
5. A physical completion cannot clear an unresolved identity/evidence hold
   or autonomously authorize financial settlement. Late historical meters/SoC
   may be kept as immutable receipts without regressing the completed projection.
6. An authenticated, exact historical fact correlated with a durable frozen
   start can be retained when inventory mapping drifts; unrelated or live
   operational status still demands current ownership and mapping parity.

These source checks have not been proven against Django/PostgreSQL/Go runtime.
Commercial hold issuance, exact settlement, deploy network isolation,
and virtual/physical chargers are distinct acceptance gates. The owner has
explicitly deferred running them.


## Recovery entrypoints and custody boundaries

- `hal_v1_dispatch` can issue one never-attempted command only under an explicit
  `--execute`; by default it merely reports eligible count. No automatic retry
  after any send attempt. `hal_v1_reconcile` performs authenticated exact Go
  lookups for previously attempted commands, never an OCPP send.
- Admin charge-intent inspection queries the exact Go transaction by immutable
  CMS start-intent ID, checks CPO, command, charger, connector and OCPP identities,
  then records a safe observation. It does not materialize an ACTIVE session: the
  immutable Go fact (or its correctly verified replay) remains mandatory.
- Signed facts whose connection to an already persisted intent is proven are
  accepted as historical evidence even after mapping changes; unrelated new
  connection/status facts still require current synchronized mapping.
- A completed physical transaction can leave a CMS stop command unresolved;
  the completion fact must match any claimed stop-command identity before that
  command is labeled MATERIALIZED. A session with unresolved evidence stays in
  reconciliation even after its meter-stop is durably recorded.
- Actual charging wallet holds, business settlement, and authorization to
  issue charge permits are **not** implemented in this HAL boundary package.
  They require the separate CoW commercial service and a specifically approved
  money-policy design. Without them, a source-only HAL cannot honestly be
  described as a complete commercial charging product.


## Explicit operational-staleness defaults

For customer projection, the source-only proposed defaults are: connection
900s, connector 900s, meter 120s, SoC 120s. These are configurable with
separate bounded settings. FRESH requires an online, non-stale connection,
current synchronized frozen mapping and a recent independent sample. Neither
charger status nor meter nor SoC is interpolated. A true online connection
with expired observation is labeled STALE; this is not a connectivity SLA.
A safety-critical physical stop must rely on exact transaction identities,
not on a cached connector status.


## Lifecycle blocker terminality

A historical start intent remains `ACTUALLY_STARTED` after its physical session
has completed. Operational asset maintenance and duty closure must not be blocked
forever solely by that historical intent. The source-review installer therefore
updates both the shared admission predicate and the driver duty predicate to
exclude only `ACTUALLY_STARTED` intents whose exact one-to-one session is
`PHYSICALLY_COMPLETED` or `SETTLED`. Any absent session, unresolved command,
active physical transaction or explicit reconciliation remains a blocker.
This does not resolve the separate service-job and financial settlement gates.


## Credential lifecycle

Once an authenticated, correlated physical `transaction.started` fact
is committed, the one-use encrypted credential on the start intent is cleared
while the non-reversible SHA-256 verifier remains for exact fact replay.
The inbound signed immutable event is persisted with its idTag encrypted;
no raw idTag is exposed in user/Admin REST or source diagnostics. This is
not a rotation or re-key procedure for preexisting secrets: this new schema
has not yet been applied or used in the owner's environment.


## Pre-send mapping drift handling

The guarded worker never retries an unchanged `PERSISTED` start indefinitely
when its original physical mapping has changed. Before any network call, a
current/frozen-mapping mismatch moves the command, intent, and permit to
reconciliation; no source code silently reassigns the target gun. Operator
review is required. An expired credential or permission window is also
closed as an unsent command, while financial hold release remains an upstream
ledger responsibility.
