# Local frontend CORS hotfix (2026-10-28)

Source fix for the published Foundation 2 backend revision
`58c36ba1986895edeb41dcb1f2fd267d66907efc`.
The live VPS2 API originally returned HTTP 405 to a browser's `OPTIONS`
preflight, so an off-origin local FE could not send ordinary JSON/Bearer calls
even though same-origin Swagger worked.

## Explicitly authorized origins

Only these six local browser origins are enabled:

```text
http://localhost:5173
http://localhost:8080
http://localhost:8081
http://127.0.0.1:5173
http://127.0.0.1:8080
http://127.0.0.1:8081
```

Exact scheme, hostname, and port matching applies. `https://localhost:5173`,
`http://localhost:5174`, `Origin: null`, and arbitrary hosted FE domains are
**not** automatically allowed. Add future FE origins only through a separately
reviewed policy change. CORS is a browser read policy, not an IP-based firewall
or an authorization mechanism.

For `/api/v1/*`, `/health/*`, `/version`, and `/openapi.yaml`:

- Valid browser preflight returns HTTP `204 No Content` without authentication.
- Allowed methods are `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, and `DELETE`.
- Allowed request headers include `Authorization`, `Content-Type`,
  `Idempotency-Key`, `If-Match`, `Accept`, `X-Request-ID`, and ordinary client
  cache headers. Other request headers are rejected at preflight.
- Approved actual requests (including 401/403/422 responses) receive the exact
  `Access-Control-Allow-Origin`. Browsers can read `ETag`, `X-Request-ID`, and
  `Retry-After` response headers.
- There is deliberately no `Access-Control-Allow-Credentials`; browser FE
  clients should use explicit Bearer authorization instead of cross-origin
  cookies. Neither `*` origins nor `*` request headers are enabled.
- Preflight changes no data. Real requests continue through their existing
  authentication, RBAC, method, body, and rate-limit checks.
- The VPS2 Caddy Cloudflare-source restriction, Swagger/UI, and SQL are
  **unchanged** by this patch.

## Verification and deployment

After applying the patch locally, run:

```sh
go test -count=1 ./internal/api ./docs/contracts
go test -count=1 ./...
go vet ./...
go build ./cmd/server
```

After explicitly deploying the new binary to staging, a read-only smoke check
is:

```sh
curl -i -X OPTIONS 'https://dev-evcowbbe.transev.site/api/v1/app/auth/login' \
  -H 'Origin: http://localhost:5173' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type,authorization'
```

Expected: HTTP `204`, exact `Access-Control-Allow-Origin`, permitted methods
and headers, `Vary: Origin` plus preflight vary headers, and no body.
Also verify a disallowed origin receives no allow-origin header, and that an
actual local FE request still needs a valid Bearer token.

Changing source files without building and activating a release **does not**
change VPS2 behavior. The separate Fleet inventory patch is not implicitly
applied or deployed by this hotfix.
