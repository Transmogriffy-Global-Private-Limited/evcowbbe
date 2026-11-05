# Driver-side development

**Entrypoint:** `cow/drivers/router.py` exports `build_router()`; `cow/api.py`
mounts its result at `/api/v1/driver`. It currently mounts shared verified
identity/2FA endpoints; driver business routes are intentionally not invented.

Add driver-only request handling here. Use central `DutySession`, `DriverProfile`,
vehicle/charger pairing, eligibility, zone assignment and service/job state
services. Never self-certify dispatch availability or accept a job without
verified eligibility and concurrency safeguards. Never issue physical HAL
commands from a role route without approved core admission.

See `docs/ROLE_MODULE_ARCHITECTURE_20261105.md`.

## Working branch

Driver work currently belongs on `anubhab-work` or a reviewed PR against `main`. No separate permanent driver branch exists.
