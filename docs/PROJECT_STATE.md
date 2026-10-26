# Project state — 26 October 2026

Scope: Initial human authentication foundation; three auth planes, email/password, email verification, rotating bearer token sessions, opt-in email OTP MFA, password change/reset, SMTP and templates, PostgreSQL schema. This replaces the previously emptied *local* Go repository tree, not the hosted GitHub repo or VPS2 runtime.

Source basis: Charger on Wheels sole-source blueprint 2026-09-24, normative annexes 11, 15, 17, 22, 44. No domain/business work implemented yet. Admin capabilities and driver operational approval are outside this slice.

Validation: Go source syntax and cross-package interfaces verified in the generation environment using isolated temporary API-compatible *stubs*. Real third-party modules cannot be downloaded here. Local Go 1.23 is insufficient for package's Go 1.25+ target, so real dependency build, PostgreSQL tests and SMTP acceptance must be completed on developer Windows as prescribed in scripts/continue-setup.ps1 after scripts/configure.ps1 (unit tests and optional separately provisioned live PostgreSQL integration acceptance). No release or Git commit/push executed by this package.

Risks/gates: No transactional mail outbox/retries, password reset notice best-effort, frontend CORS intentionally closed, no admin business privilege handlers yet, no production TLS proxy or operational deployment. Do not confuse compile-only stubs with true dependency tests.

## External-service setup change — 26 October 2026

At user request, removed all Docker and Mailpit dependencies. Configure existing PostgreSQL + genuine TLS-enabled SMTP through hidden prompts in `scripts/configure.ps1`, retaining previously generated peppers; follow with explicit `continue-setup.ps1` preflight/tests and interactive migration consent. No database or container is auto-provisioned. Added a read-only SMTP AUTH handshake probe and an opt-in real test-email sender; SMTP delivery and PostgreSQL integration remain unverified until run against user's services. Existing GitHub and VPS2 untouched.
