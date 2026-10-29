# Go HAL plug-in contract: `cow-hal.v1`

**This contract is a proposed and independently testable interface. The future Go
HAL has NOT implemented or passed it yet. Physical charger integration therefore
cannot be claimed complete.** Versioning is explicit. Do not modify behavior
in place and assume compatibility.

## Authority and topology

- Python CMS owns jobs, driver/vehicle assignment, pricing, payments and audited
  commercial events. It does not speak OCPP or create physical transaction IDs.
- Go HAL owns charger WebSocket/OCPP connections, physical transactions, meter
  observations, command execution, raw logs, durable outbox, and recovery. It
  uses **its own DB** and independent process, on the same VPS2 if capacity permits.
- CMS and HAL communicate only over authenticated, bounded HTTP contracts.
  No shared database and no generic "call method on HAL object" shortcuts.
- Simulator mirrors `cow-hal.v1` semantic commands/facts. Its tests are
  necessary but insufficient: actual Go HAL must pass the same conformance suite.

## Commands: Python CMS -> Go HAL

`POST /v1/operations` with immutable idempotent command identity:

```json
{
  "contract":"cow-hal.v1",
  "command_id":"11111111-1111-4111-8111-111111111111",
  "charger_identity":"physical-001",
  "kind":"StartCharging",
  "parameters":{"connector_number":1}
}
```

Semantic command `kind` allowlist: `StartCharging`, `StopCharging`, `Reset`,
`UnlockConnector`, `SetChargingLimit`, `ChangeAvailability`, `GetCapabilities`.
The Go adapter selects OCPP 1.6/2.x/vendor mapping internally. An HTTP 202
with matching command identity only means durable HAL queue admission.
Same command ID/body returns the SAME logical result without redelivery;
same ID/different body gives 409 and no effect. Retry after timeout is NOT
allowed until exact reconciliation proves it safe. A rejected response must
have a documented definitive **no side effect** meaning, else CMS records
`OUTCOME_UNKNOWN`.

`GET /v1/operations/{command_id}` is the read-only exact lookup for uncertain
commands; it must authenticate and distinguish queued, attempted, rejected,
physical facts, and unknown outcomes, not manufacture certainty.

## HAL facts: Go HAL -> Python CMS

`POST /api/v1/internal/hal/facts` on CMS, signed over **raw bytes** with:

- `X-HAL-Timestamp`: Unix seconds, 120-second skew limit.
- `X-HAL-Signature`: lowercase HMAC-SHA256 hex over
  `ASCII(timestamp) + b'.' + raw_request_body`.
- HTTPS or loopback verified at deployment, shared key >=32 bytes in separate
  private configuration, independent from user auth secrets. Key rotation needs
  dual-key migration, not an unauthenticated interval.

```json
{
  "contract":"cow-hal.v1",
  "event_id":"22222222-2222-4222-8222-222222222222",
  "kind":"TransactionStarted",
  "charger_identity":"physical-001",
  "emitted_at":"2026-10-29T09:30:00Z",
  "data":{
    "command_id":"11111111-1111-4111-8111-111111111111",
    "transaction_id":"ocpp-exact-789",
    "connector_number":1,
    "meter_start_wh":6000
  }
}
```

Normalized facts: `ChargerConnected`, `ChargerDisconnected`,
`ConnectorStateChanged`, `TransactionStarted`, `TransactionEnded`,
`MeterSampleObserved`, `FaultObserved`, `CommandAccepted`, `CommandRejected`,
`CommandOutcomeUnknown`, `CapabilityChanged`.

The `event_id` is stable across Go HAL retry. CMS requires exact same event ID
and payload; changed payload for same ID is a hard 409 integrity conflict.
Same event ID/body is a deduplicated 202. Meter/end may arrive before start:
signed evidence is stored as DEFERRED, and applied after the matching start
appears, never attributed to a guessed job. Unknown chargers/commands are
rejected. Unapplied events require operations monitoring.

**No invented effect:** `CommandAccepted` is NOT `TransactionStarted`.
A button click is NOT `TransactionStarted`. `TransactionEnded` is NOT job
completion or payment settlement. An unknown transaction cannot be assigned
by charger proximity or user-supplied transaction ID. Multiple physical
transactions may belong to one `ServiceJob`.

## Version negotiation and regression lock

Before Go HAL is attached:

1. Implement `cow-hal.v1` exactly, run Python pure tests and Go adapter tests
   on the same versioned JSON fixtures; validate HMAC and replay behavior.
2. Run a real PostgreSQL transaction-boundary suite on the Python CMS.
3. Exercise simulated start -> accepted -> actual start -> meters -> fault ->
   stop -> end, including lost reply, reconnect, out-of-order events,
   duplicate retry and two independent transactions per job.
4. Compare normalized records and immutable job/payment/compensation results.
5. Shadow/test against a disposable charger/simulator before any physical
   charger. Confirm stop/safety behavior and recovery manually with evidence.
6. For future breaking changes, add `cow-hal.v2` explicit adapters and
   contract-version dual-running, not implicit interpretation changes.

Current limitation: dispatch, billing, compensation and full lifecycle
orchestration are not implemented end-to-end in this source package.
The HAL binding is *designed*, not proven against a real Go implementation.
