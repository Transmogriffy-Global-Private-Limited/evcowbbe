# Sprint 9 C1: operational Driver recruitment vertical slice

## Deployment status

This is implementation code, not a substituted specification. It is **not production released** until isolated PostgreSQL validation, migration review, local/private-storage provisioning, and controlled deployment pass. It does not enable HAL or Driver dispatch.

## End-to-end behavior

- Driver uses existing verified **Driver bearer** (PENDING is allowed for recruitment), creates/edits the application with `PUT /api/v1/driver/application`, uploads real PDF/JPEG/PNG documents via multipart, submits with `POST /api/v1/driver/application/submit`, checks reviews and accesses own files. `GET /api/v1/driver/application` does not create a row.
- Admin uses existing role `DRIVER_RECRUITMENT` / capability `drivers.review` for application and document reviews; `drivers.manage` configures policy and completes onboarding. Read-only/SUPPORT staff cannot inspect KYC documents.
- Admin must first create then activate a versioned `DriverRecruitmentPolicy` with required document types, an explicit decision reference and a `scan_required` choice. When no active policy exists, submission returns `409 DRIVER_POLICY_REQUIRED`; this is a real configurable gate, **not** a hardcoded assertion about Indian law.
- Application transitions are explicit, idempotent and versioned. Reviews are durable; superseded document versions and their reviews are retained. Terminal rejected/withdrawn applications are not silently reopened; later recruitment cycles need a subsequent decision.
- `ACTIVE` recruitment state may set the Driver Principal to ACTIVE and create the DriverProfile if missing, but **does not** mark `DriverProfile.eligibility` as ACTIVE. C2 owns operational document clearance, suspension, schedules, licence invalidation and duty eligibility.
- Document MIME must match content signature and filename extension; bytes are stored under server-generated opaque local keys and are streamed only after authorization. Admin downloads are audited. There is no public bucket, public document URL or client-selected storage path.

## Storage and provider port

The current provider adapter is `cow/services/private_documents.py`. It defines a minimal `put/open/verify/delete_uncommitted` contract; replace it at this one boundary for a privately configured S3-compatible storage provider. No new domain columns or HTTP IDs are required for that replacement. Unsupported provider configurations fail with `503` instead of silently using web-accessible paths.

For local development set in the **ignored** `.env` used by your environment runner, or securely inject into the runtime process:

```
STORAGE_PROVIDER=LOCAL
PRIVATE_STORAGE_ROOT=C:\\Users\\AnubhabDey\\evcowbbe-private-documents
```

Create that directory outside the repository and any static/web roots before uploading. Restrict NTFS access to the service account plus authorized administrators; do not grant Users/Everyone broad read permissions. On Linux, provision a service-owned directory with permissions `0700` and ensure backups are private and encrypted. **Do not publish the storage root via Caddy or Cloudflare.**

`UPLOAD_MAX_KYC_BYTES` in the blueprint is 10 MiB. This implementation enforces that size during streaming regardless of client-reported size. MIME whitelist is PDF, JPEG and PNG only. Files are returned as attachment/octet-stream, with server-generated filenames. A malicious/suspicious file may still pass magic-byte checks: scanning is not implemented in C1. If policy has `scan_required=true`, documents remain `NOT_SCANNED` and approval is blocked until a verified scanner adapter is integrated. Setting `scan_required=false` is an explicit recorded business policy decision, not an assertion that scanning is unnecessary.

Filesystem objects are created before DB receipt commit to allow streaming. Failures and idempotency replays remove uncommitted objects; an abrupt process termination can leave a random orphan. A future retention/repair worker must reconcile unreferenced files against DB evidence before deletion. The program never calls deletion for committed documents.

## Next-system integration ports

| Boundary | C1 implementation | Follow-up contract |
| --- | --- | --- |
| Recruitment policy | Versioned, immutable policy and Admin activation | Legal/HR decides permitted required document set and scan requirements |
| Storage provider | Private local adapter with opaque key | S3-compatible implementation and secret/backup/deletion policy |
| Scanner | `scan_status=NOT_SCANNED`; approval fail-closed if required | Verified CLEAN evidence adapter with retry/reconciliation |
| Notification delivery | Append-only application events with `event_key`, `delivery_state=NOT_CONFIGURED` | Stage E outbox + notification projection, no false “sent” status |
| Operational activation | `DriverProfile.eligibility=PENDING`, licence valid-until copied only from accepted document | Stage C2 explicit eligibility and suspension clearance under Stage B gate |
| Schedules and duty | Unchanged Stage B gate | C3 duty claims/schedules use same gate and role checks |
| Privacy/retention | No hardcoded KYC expiration or destructive cleanup | Approved legal retention/export/delete decision and private backups |

## HTTP contract

Every JSON mutation requires `Content-Type: application/json` and `Idempotency-Key` (16-128 visible ASCII). Application edits, document uploads and all application state transitions require a quoted current `If-Match: "N"` where N is the application version (use `"0"` for the first edit). Upload is `multipart/form-data` with `document_type`, optional `expires_on`, and file. Upload key must be reused only on an exact logical retry of identical content. Responses return a versioned `data` payload. Errors use existing `DomainError` envelope, including conflict/expiry/status conditions.

Driver application endpoints:

```
GET  /api/v1/driver/application
PUT  /api/v1/driver/application
POST /api/v1/driver/application/documents
POST /api/v1/driver/application/submit
POST /api/v1/driver/application/withdraw
GET  /api/v1/driver/application/reviews
GET  /api/v1/driver/application/documents/{document_id}/download
```

Admin recruitment endpoints:

```
GET  /api/v1/admin/driver-application-policies
POST /api/v1/admin/driver-application-policies
POST /api/v1/admin/driver-application-policies/{policy_id}/activate
GET  /api/v1/admin/driver-applications
GET  /api/v1/admin/driver-applications/{application_id}
POST /api/v1/admin/driver-applications/{application_id}/start-review
POST /api/v1/admin/driver-applications/{application_id}/request-action
POST /api/v1/admin/driver-applications/{application_id}/approve
POST /api/v1/admin/driver-applications/{application_id}/reject
POST /api/v1/admin/driver-applications/{application_id}/begin-onboarding
POST /api/v1/admin/driver-applications/{application_id}/complete-onboarding
POST /api/v1/admin/driver-applications/{application_id}/documents/{document_id}/review
GET  /api/v1/admin/driver-applications/{application_id}/documents/{document_id}/download
```

## Verification/release

- `main` and `anubhab-work` source baseline must equal `0cbddba300f7b2b25498f1d0b3a78ba70e05f131`. No Customer module edits.
- Source installer is guarded and writes only reviewed C1 paths. Historical migration `0010` is untouched; `0011` creates recruitment tables and immutable-evidence triggers. Never migrate Aiven automatically.
- Run Django checks, model/migration parity, all pure suites, live generated OpenAPI JSON/YAML contract, isolated PostgreSQL 17 full integration, 0011 forward/reverse/reapply, and Stage B regressions. Confirm same test DB identity and shutdown.
- Configure and validate private storage ownership/ACL separately before any KYC upload. No real customer/Driver sensitive information belongs in tests, Git, logs or this package.
- Review SQL, API contracts, private-storage behavior, rollback behavior and errors before staging/pushing or rehosting. **No physical HAL commissioning is authorized.**
