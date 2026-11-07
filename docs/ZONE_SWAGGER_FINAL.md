> HISTORICAL Sprint 7 Swagger contract: superseded for new authoring by `/api/v1/admin/commercial/*`. The older Zone endpoints remain for compatibility.

# Sprint 7 Swagger: exclusive Zone composite tariff API

**Status:** source-only contract update, applied after Sprint 7 migration 0009. No new migration or database reset. The previously installed 15-file Sprint 7 source remains; this package changes only the Admin API/documentation/tests for the agreed product decision.

## Supported tariff mode

Only **Zone composite tariffs** may be authored, published, and simulated through the Admin HTTP API. Both service channels remain supported:

- **ROAMING**: Zone is owned by an authorized duty assignment; composite plan may include transport, disjoint distance slabs, one primary charging basis (ENERGY_KWH or TIME_MINUTE or SESSION), and optional event charges.
- **ON_SITE_QR**: Zone is owned by a charger assignment, with no transport/distance amount; charger-QR proof is a separate future trusted evidence requirement.

`PricingPolicy` continues to own **one** publication/version ledger. `ZoneTariffPayload.plan` is the immutable Zone tariff content. The shared exact-integer pricing engine remains the arithmetic implementation, not an alternative price-authoring mode. Existing legacy pricing data are not deleted. Old `/api/v1/admin/pricing/policies` HTTP routes are authenticated tombstones (`410 GENERIC_PRICING_RETIRED`) and are absent from OpenAPI. Legacy customer quote creation and acceptance are also retired (`410 LEGACY_QUOTE_RETIRED`), both at HTTP and service boundaries, until Zone commitment is implemented. Existing legacy data is preserved. These are intentional API breaking changes.

## Swagger documentation

- Swagger UI: `http://127.0.0.1:18080/api/swagger/`
- JSON: `http://127.0.0.1:18080/api/openapi.json`
- YAML: `http://127.0.0.1:18080/openapi.yaml`
- Base URL: use the same origin. `0.0.0.0` is a bind address, not a browser address; a managed Windows firewall may continue to block other LAN clients without affecting local Swagger.

All 18 Zone Admin HTTP operations have typed success shapes: Zones, assignments, GST, tariff ledger and composite plan, and simulation-only price breakdown. The Swagger groups are Zones, Assignments, Zone GST, Zone Tariffs and Simulation. Error envelopes and standard error statuses are annotated. Pydantic/DB domain constraints can add checks beyond OpenAPI's structural schema.

## Workflow

1. Admin login/2FA, authorize **Admin** bearer token in Swagger.
2. Create Zone draft at `POST /api/v1/admin/zones`, and publish it.
3. Add duty or charger assignment, `POST /api/v1/admin/zones/assignments/{kind}`. Include `Idempotency-Key` on every mutation.
4. Create and approve a channel-specific Zone GST binding with actual approved seller and legal decision references. The zero values in Swagger examples are not certified tax rates.
5. Create a composite plan via `POST /api/v1/admin/zones/tariffs`; `plan.zone_id` equals Zone `zone_key`, **not** the Zone UUID. `rules` is intentionally empty on the shared ledger.
6. Retrieve the policy's `ETag` from tariff create/GET, then publish with quoted `If-Match` and a unique `Idempotency-Key`. Changes in versions require a new plan revision.
7. Run `POST /api/v1/admin/zones/preview` only to inspect the configured tariff. Results include `simulation_only: true`, `may_charge_customer: false`, `may_accept_quote: false`, `may_command_hal: false`.

This slice does **not** implement customer quote commitment, trusted GPS/QR evidence, wallet settlement or physical HAL execution. The old non-Zone customer quote/acceptance endpoints are intentionally disabled (410) and hidden from OpenAPI. Customer service-request intake and reads remain. Zone binding quote/acceptance must be developed and tested before any customer payment authority.

## Verification

Source check after installation:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe -m scripts.verify_zone_swagger
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_zone_swagger_static.py' -v
git diff --check
```

Use the existing **isolated PostgreSQL** runner for complete integration acceptance, not shared Aiven `defaultdb` as a disposable test database. Existing older `tests/integration/test_admin_rbac_pricing.py` is updated to check the deliberate old-HTTP retirement instead of expecting generic authoring to succeed.

If the local Django server is running with `--noreload`, restart it to load the new source. Firewall/IT approval is independent from Swagger correctness.
