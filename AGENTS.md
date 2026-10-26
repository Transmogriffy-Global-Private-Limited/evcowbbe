# Source authority for this auth foundation

This repository is the Charger on Wheels application, not a CI/CD system. Implement user-requested features against the project blueprint and normative annexes 11, 15, 17, 22, 44. Preserve authorization truth, security invariants, and domain separation. Do not invent rights from a principal's role label. Do not weaken token or OTP validation to facilitate testing.

Do not add secrets or `.env` to Git. Every migration is immutable after applying. No destructive migrations or silent schema changes at server startup. Never log credentials, tokens, SMTP payloads or DATABASE_URL. Always test before suggesting manual deployment. Do not modify or reconnect to VPS2, GitHub branch configuration, or automated deployment infrastructure unless expressly authorized.

External services only: scripts must never require/start Docker, PostgreSQL or Mailpit. Provision DB and real SMTP out-of-band, configure `.env`, validate read-only, and require explicit confirmation before migrations. Preserve existing `.env` peppers when updating; never write real secrets to tracked files.
