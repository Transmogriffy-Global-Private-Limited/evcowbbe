# User-side development

**Entrypoint:** `cow/users/router.py` exports `build_router()`; `cow/api.py` mounts
its result at `/api/v1/app`. Existing master-data endpoints live in
`master.py`, charging request/quote/job endpoints in `charging.py`, validation
in `contracts.py`. The core Django models and migration ledger are centralized.

Work here on the user journey, customer API endpoints, validation and tests.
Do not copy or fork pricing, wallet, driver eligibility, zone, GST, HAL or
shared authentication. Propose new shared-service interfaces to the backend
owner, and wait for reviewed integration. Test OpenAPI parity and actual
principal-plane authorization on every change.

**Important:** current `create_quote` is legacy pre-driver behavior, not the
binding frozen driver-zone quote. No wallet debits or live charging are enabled.
See `docs/ROLE_MODULE_ARCHITECTURE_20261105.md`.

## Working branch and review

Work on `abhranil-pal-evcowbbe` created from reviewed `main`. Send PRs to `main` for core review. Your primary implementation surface is `cow/users/` and relevant user tests/docs. Never change central schema, auth, dispatch, money, tax, HAL or shared policy without a reviewed core contract.
