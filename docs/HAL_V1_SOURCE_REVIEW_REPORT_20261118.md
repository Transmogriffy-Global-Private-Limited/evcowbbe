# CoW HAL v1: Consolidated post-install source audit and correction

Date: 2026-11-18 (Asia/Kolkata)
Status: SOURCE-REVIEW REPAIR CANDIDATE. NOT EXECUTED, MIGRATED, COMMITTED, DEPLOYED OR ACCEPTED.

## Evidence and scope

- User-proven starting tree: clean `anubhab-work` at `58a4144d8f8a6a26ed718028068029fda24ef97b`, then original 16-file candidate installed and verified without tests/migrations.
- Exact basis: `COW_HAL_V1_DJANGO_SOURCE_CANDIDATE_20261118.zip`, SHA-256 `346e1e851f5609154005af0c9b770c5aaaa2cf45f73c5a373bcc5a0374785c01`.
- Read-only Django source review: `Transmogriffy-Global-Private-Limited/evcowbbe` at the above commit, plus original locally extracted candidate files and approved `0016` migration.
- Read-only Go source: `Transmogriffy-Global-Private-Limited/ocpp-hal-go-new` at `1908c934c0e2570b81523e8ef3fe20be1e519da0`, specifically `docs/contracts/CMS_HAL_CHARGING_V1.md`, `internal/store/v1_lifecycle_postgres.go`, `internal/httpapi/v1.go`, `internal/httpapi/v1_response.go`. No Go edit authorized.
- Source-only audit: AST parsing, exact anchor / patch inspection, per-file SHA-256, ZIP and installer structural review. NO Django import, test run, `manage.py`, database, network, charger, OCPP or payment call.

## Corrected as a single guarded overlay

| Area | Found in installed V1 | Consolidated correction |
| --- | --- | --- |
| Credential crypto | Reused `encrypted-outbox`/email Fernet domain | New `hal-v1-one-use-credential` derivation purpose; same security source, independent derived key; sensitive idTag cleared on accepted physical start; digest retained |
| Start expiration | Command expiry could outlive financial permit | Command expiry bounded by permit; credential expires earlier; pre-send expired state handled without network side effect; no automatic wallet release |
| Physical admission | Fixed-QR lacked legacy command/session overlap guard | Checked legacy active session, old HAL commands and unresolved Admin HAL operations before new intent |
| Stop during mapping drift | Required current `SYNCHRONIZED` mapping, potentially forbidding safety stop | Frozen start mapping is used to stage exact physical stop; new mapping cannot silently substitute connector; HAL may still reject if historical identity not enrolled |
| Stop provenance | Physical end unconditionally marked local STOP `MATERIALIZED` | Nullable Go stop command identity is matched to exact attempted CMS STOP; mismatch holds for reconciliation; autonomous stop without a CMS stop is valid physical evidence |
| Command send | Unreadable credential got labeled attempted; malformed successful response risked exception | Decrypt before durable attempt; malformed Go 202 response stored as uncertain; immediate protocol state distinguished from durable admission |
| Late facts and command ordering | Later acknowledgement could clear an existing reconciliation state | Reconciliation hold is monotonic absent independent accepted resolution; late start past hold/command expiry is retained as unsafe physical truth |
| Physical completion | Completed facts could erase reconciliation and enable settlement | Stop evidence recorded, but held session/permit stays reconciliation; never represents financial settlement |
| Historical facts | Current mapping parity required even after physical session began | Exact persisted command/start and frozen mapping chain used to accept historical facts; new connection/status still require current mapping |
| Replay after remapping | Pending signed receipt needed currently synchronized mapping | Replay verifies against durable frozen owner/connector chain; retains immutable receipts without guessed ownership |
| Late meter/SoC | Post-completion historical sequences produced unnecessary reconciliation | Older/equal, temporally valid readings are retained as immutable evidence but cannot regress completed projection; contradictions still hold |
| Completion replay identity | A quarantined completion could be overwritten by a different completion fact on a later replay | Any first completion fact permanently pins its identity, end meter and stop timestamp, even while reconciliation is held |
| Zero-SoC truth | A real 0% SoC could be exposed as missing via truthiness | SoC output distinguishes numeric 0 from unknown and renders an exact decimal string |
| Operational blocking | `ACTUALLY_STARTED` persisted forever even after completed session | Fleet and duty blockers exclude only proven completed/settled one-to-one session; missing or uncertain session remains blocked |
| Admin stop | No current Admin path for charging stop; ability to supply `owner=None` dangerous | Typed Admin-only stop endpoint with fresh RBAC locked before operational gate and no owner bypass |
| Go exact transaction inspection | Only command query existed, and transaction source inspection was not exposed | Admin exact transaction-for-intent query compares CPO/charger/connector/start/command identities and audits; cannot fabricate an ACTIVE session |
| Recovery | No bounded source-owned reconciliation sweeper | New explicitly opted-in management command reconciles attempted commands by exact Go lookup, never resends |
| Customer projection freshness | Same 900-second threshold for connection/meter/SoC | Separate explicit connection/connector/meter/SoC settings with frozen-mapping freshness guard and truthful stale/unknown fields |
| Audit ownership | Most owner/Admin intent/stop/inspection events had null actor | Sanitized audit records include the actual authenticated Principal when present |

## Still externally blocked, intentionally not fabricated

1. **Commercial authorization issuer**: no committed, authoritative zone tariff/tax, verified QR/roaming location, or ledger-backed charging wallet-hold issuance flow is present in the frozen CoW source. Merely assigning `HALV1ChargingPermit.state='RESERVED'` cannot become proof of real funds. The start route requires a durable trusted upstream permit and is default disabled. This overlay does not invent a money provider, paper ledger, tariff, or billing policy.
2. **Money settlement/refund**: no approved atomic exactly-once settlement or hold-release port exists for `PHYSICALLY_COMPLETED`; the HAL records physical truth and `SETTLEMENT_PENDING`, never auto-settles. Requires the business/financial workstream.
3. **Migrations**: existing new migration `0017` is source-only and unapplied. This overlay leaves the model/migration field definitions unchanged. The accepted deployed migration `0016` remains untouched.
4. **Acceptance**: no executable Django/PG/Go acceptance, virtual charger, real charger, operational environment or deployment evidence has been produced here. Contract compatibility is source-level evidence only.
5. **Historical Go mapping removal**: frozen identity allows a safe stop to be staged but does not magically restore a removed Go mapping; this needs exact operator reconciliation and hardware-independent Go acceptance later.

## Release conditions and next handoff

Preserve feature flags OFF; no crediting payments from command ACK; no customer live exposure. Source update is pre-approved for review **only**, not promotion. Explicit later gates: model/migration parity, scratch PostgreSQL schema + race suite, Django/Go authenticated HTTP contract (no charger needed), commercial writer/settlement acceptance, then virtual and physical chargers when authorized. The owner has deferred all runtime tests here.
