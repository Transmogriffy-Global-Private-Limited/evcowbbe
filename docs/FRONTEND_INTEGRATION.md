# TransEV Charger on Wheels: frontend integration contract

> Scope: EVCOWBBE Django **development** API. This guide describes what the running backend advertises, not acceptance of payment processing, full dispatch orchestration, a physical HAL, or a complete commercial workflow.

## Canonical live documentation

| Resource | URL | Consumers |
| --- | --- | --- |
| Swagger UI | `https://dev-evcowbbe.transev.site/api/swagger/` | Developers exploring and invoking operations |
| OpenAPI JSON | `https://dev-evcowbbe.transev.site/api/openapi.json` | OpenAPI generators, Swagger UI, CI |
| OpenAPI YAML | `https://dev-evcowbbe.transev.site/openapi.yaml` | AI coding assistants, generators, CI |
| Readiness | `https://dev-evcowbbe.transev.site/health/ready` | Infrastructure; **not** hardware readiness |

**Contract authority:** code running in `/srv/evcowbbe/current-python` plus corresponding GitHub `main` revision. The two OpenAPI encodings must be structurally identical. Path existence is not proof of business acceptance; inspect each operation's declared schemas, constraints and warnings.

## Auth, sessions and role boundaries

1. Pick one session plane: `app` (customer), `driver`, or `admin`. Never swap tokens between planes.
2. Call `POST /api/v1/{plane}/auth/login` with JSON `{ "email": "...", "password": "..." }`. The result discriminates on `data.two_factor_required`: false returns `data.tokens`, true returns a challenge ID. For challenges use `/api/v1/{plane}/auth/verify-otp`; do not blindly assume the token branch.
3. Use `Authorization: Bearer <opaque access token>` on declared protected routes. This is an opaque token, **not** a JWT. Use the corresponding plane's refresh route and handle revoked/reused credentials as a sign-out condition. Never log tokens or OTP values.
4. For Admin calls, authenticated identity is not equivalent to authorization. Capabilities such as `fleet.read`, `fleet.manage`, `pricing.read`, `pricing.manage`, `audit.read`, and `system.settings.manage` are enforced **on the backend**. The FE may hide controls but must still handle denied responses.

The schema's Security section is authoritative per operation; the `Authorize` dialog may expose distinct schemes for different routers. Choose the matching scheme.

## Client error and retry discipline

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Invalid request fields",
    "request_id": "00000000-0000-4000-8000-000000000001",
    "details": {}
  }
}
```

The above is an **illustrative error-envelope shape**, not a promise of that error code on every operation. Use the *actual* response's status and `error.code`, never parse `message`. Record `X-Request-ID` for diagnostics. Distinguish 401 (sign-in/auth), 403 (permission), 409 (business conflict), 412 (stale version), 428 (required precondition/idempotency key), 429 (rate limited; honor `Retry-After` where present), 5xx (uncertain outcome), and network timeouts (unknown outcome).

**Idempotency:** for operations declaring `Idempotency-Key`, generate a separate 16-128 character printable ASCII key for each logical mutation; reuse the same key only for a byte-for-byte-equivalent retry of the same operation and payload. An HTTP 5xx or network timeout does not prove a mutation failed. Do not generate a fresh key and immediately resubmit a potentially completed payment/charging request. For customer saved-master-data **creates**, the key is optional for historical compatibility; FE should nevertheless supply one.

**Optimistic concurrency:** for an operation declaring `If-Match`, send the exact quoted ETag/version from the latest resource response. Customer profile reads return a `version` field; a new profile can start at `"0"`. Customer vehicle/address detail reads and accepted creates return `ETag` headers. On 412, refetch, reconcile the user's edits and only retry if the user wants to apply them to the new state.

## Customer request examples

The customer body schemas are projected from the Pydantic validators actually
called by the Django handlers. JSON Schema does **not** express every
model-level rule: nonempty patches, non-null selected fields, EV charging-mode
consistency and uppercase country-code checks remain server-enforced. The FE
must still handle 422 from these validators.

These requests illustrate **input** fields only. No sample key, identity, address or token is an actual provisioned record.

**Add a saved EV**

```http
POST /api/v1/app/vehicles HTTP/1.1
Authorization: Bearer <customer-access-token>
Content-Type: application/json
Idempotency-Key: frontend-ev-create-0001

{"make":"Example Manufacturer","model":"Sample EV","supports_ac":true,"supports_dc":false}
```

The runtime returns HTTP **201**, `data` containing the saved vehicle, and an `ETag`. The EV capabilities are unverified customer data; do not automatically declare charger compatibility.

**Create an address**

```http
POST /api/v1/app/addresses HTTP/1.1
Authorization: Bearer <customer-access-token>
Content-Type: application/json
Idempotency-Key: frontend-address-create-0001

{"address":"Example Street, Sample City, India","latitude":"22.5726000","longitude":"88.3639000","saved":true}
```

Also returns HTTP **201**. Coordinate response fields are formatted as decimal strings with seven fractional digits. An account's first saved address may be selected as default. PATCH/DELETE/default changes require the quoted current version and idempotency key.

## Admin fleet, pricing and charging

- **Fleet:** use dedicated Admin fleet vehicle, charger, connector and pairing endpoints. Query/pagination, ETags, mutating headers and response schemas are in OpenAPI. Maintenance and history are distinct from live charger state.
- **Pricing:** policy drafts, publication, withdrawal, effective windows and rule priority are separately controlled. Integer amounts ending in `_paise` are INR minor units. Do not present incomplete commercial flows as completed billing or tax settlement.
- **Charging:** `POST /api/v1/admin/jobs/{job_id}/charging/start` returns HTTP **202** on accepted/queued command. This **does not prove** physical charging began; show a pending operation and wait for verified command/fact transitions. The signed HAL ingress is a **server-to-server** endpoint. Never embed its signing secret in the FE.

## Date, time, access, and implementation boundaries

- Request timestamps must honor documented timezone/offset requirements. Distinguish `created_at`, expiry, effective windows and receipt times; never derive business state purely from local browser clocks.
- Browser CORS is a separate enforcement boundary. A working Swagger page on the API host does **not** authorize an arbitrary frontend origin. Consult the backend's explicit allowlist and verify real browser preflights without permitting wildcard origins.
- Swagger indicates the **implemented route surface**, not unimplemented blueprint modules. Do not generate user screens solely from nouns in requirements documents.
- Operations marked **Documentation gap** or `x-fe-schema-detail: generic-success` do not have a complete typed success contract. Block automatic FE model generation for those endpoints until backend DTOs and integration tests are added. Do not fill missing response properties by guessing from UI designs or older Go payloads.

## CI gate before FE integration

1. `GET /api/openapi.json` and `GET /openapi.yaml` must both succeed and parse into equal objects.
2. Swagger HTML, its local CSS/JS, and the OpenAPI JSON URL referenced in the page must work.
3. Every operation must retain a stable `operationId`, per-operation auth `security`, and unique method/path, and explain its purpose, preconditions, response/error semantics, and integration status.
4. Any mutation of paths, verbs, security requirements, request fields, response body, requiredness, status sets, ETag or idempotency contract needs a versioned, reviewed compatibility decision and FE integration tests, not merely prettier documentation.
5. Run backend offline tests, Ruff and targeted isolated PostgreSQL tests before claiming the affected business flow is accepted. A passing OpenAPI schema test alone is **not** end-to-end backend acceptance.
6. Deploy only using the canonical `rehost-evcowbbe` immutable Django release operator. Verify live JSON/YAML equality and HTTP asset delivery after promotion. Preserve the rollback revision and journal.
