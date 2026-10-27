# AI development change log

2026-10-26: Created initial auth-only codebase with explicit user/customer, pending-driver, CLI admin principal; Argon2id passwords, opaque rotating sessions, hashed credentials/challenges, email verification, email OTP 2FA off by default, password recovery and revocation, configurable SMTP and versioned templates, SQL migrations, rate controls, command-line bootstrap and PowerShell local setup. No Git/VPS mutations.

## 2026-10-26 — External PostgreSQL and SMTP setup revision
Removed Docker Compose/Mailpit, split configuration and continuation stages, preserved credentials, added independent live service preflight and optional real test mail; no VPS or GitHub changes.

## 2026-10-27 — Auth foundation continuation (packaged source; not deployed)

Added additive migration 002, encrypted transactional auth-email outbox with crash-recoverable leases/retries/expiry gates and a periodic API worker, and Annex 44 Admin RBAC with seven protected API operations, explicit offline first-role bootstrap, DENY precedence, reasoned audit and mutation guardrails. No mutation of GitHub/VPS/Aiven occurred from this patch. Live PostgreSQL `_test` integration, inbox receipt, backup/restore and production migration remain unverified until executed in their appropriate environments.
