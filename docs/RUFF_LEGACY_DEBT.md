# Historical Ruff observations (2026-11-04)

The recorded Ruff findings in
`docs/verification/ruff-legacy-lint-debt-20261104.json` are retained as
historical review evidence only. They are not proof of functional errors.

**Ruff is optional, not part of the required build, test, or deployment
workflow.** The mandatory whole-repository Ruff invocation in the release
manager and its byte-pinned lint-debt unit-test gate were removed by explicit
operator request. The historical per-file configuration in `pyproject.toml`
remains available to anyone who voluntarily runs Ruff after installing it.

Application unit tests, schema compatibility checks, immutable release seals,
service-account import checks, and health verification remain mandatory. Any
future lint adoption must be a separate deliberate change, not a hidden
prerequisite to deploying otherwise tested code.
