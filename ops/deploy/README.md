# EVCOWBBE deployment control-plane foundation

`ops/deploy` is Burner 1 source for a local-only deployment control-plane
state authority. It uses Python 3.12 standard library modules and a
caller-selected SQLite database. It has no network, subprocess, Git, build,
service, migration, release-link, application-environment, SMTP, or VPS
access capability.

Run the CLI from the repository root with an explicit state path:

```bash
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 status
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 submit \
  --event push --sha <40-lowercase-hex> --branch prod \
  --actor trusted-local-operator --source-id <unique-event-id>
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 pause \
  --operator trusted-local-operator --reason "maintenance window"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 sideline <sha> \
  --operator trusted-local-operator --reason "investigating failure"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 resume \
  --operator trusted-local-operator --reason "maintenance complete"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 release <sha> \
  --operator trusted-local-operator --reason "review complete"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 retry <sha> \
  --operator trusted-local-operator --reason "reviewed retry after failure"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 cancel-retry <sha> \
  --operator trusted-local-operator --reason "retry no longer authorized"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 requests
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 history --limit 100
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 status --verbose
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 status \
  --trusted-prod-sha <externally-verified-40-lowercase-hex-sha>
```

`resume` releases pause and `release <sha>` releases an exact SHA sideline.
Neither command deploys anything. `rollback-hold <sha>` and
`rollback-release` are durable future rollback protections: while a rollback
hold is active, automatic reconciliation is blocked. Removing it resumes
selection from current durable state; it never replays an old request.

An admitted request is a local candidate, not proof that its SHA is current
remote `prod`. Normal `status` intentionally reports
`authoritative_prod_head_required` when candidates exist but no externally
verified SHA is supplied. `--trusted-prod-sha` is for controlled tests or a
future trusted executor only; this CLI cannot authenticate GitHub or inspect
the remote. Selection is exact: if the verified head is sidelined, deployment
is blocked and Burner 1 never falls back to an older accepted SHA.

SQLite permits exactly one active run globally. A planned run is fenced again
when it claims executable work, so a newly activated pause, sideline, rollback
hold, or changed verified head blocks/supersedes it safely. Burner 1 makes one
stable SHA-level attempt per selected request and does not retry it
automatically. Different ingress `source_id` values for the same SHA remain
audit history, not extra attempts. The first accepted event for a SHA is its
canonical request; later same-SHA events never relabel existing work. After a
failure, or cancellation after execution was claimed, `retry <sha> --operator
... --reason ...` creates one durable authorization that is consumed by a new
attempt for that same canonical request. A superseded or canceled-before-claim
run is safe pre-execution work and can be planned again if its SHA becomes the
verified head. A succeeded SHA requires future-executor runtime reconciliation
before another attempt because Burner 1 cannot infer actual release state. A
later verified fix-forward SHA can proceed once a failed run is terminal.
`cancel-retry <sha> --operator ... --reason ...` withdraws only an unused
authorization, preserves it as released audit history, and restores the retry
fence. Repeating cancellation is a no-op; a consumed authorization remains
historical and cannot be canceled retroactively.

The caller-provided operator identifier is audit attribution only. This local
source implementation assumes the invoking command boundary is trusted. A
future ingress/authentication layer must pass a verified operator identity.

The Python API offers migration-decision persistence but intentionally exposes
no migration execution. A future executor must derive an exact plan
fingerprint, obtain approval for that same SHA and fingerprint, recheck it
immediately before its irreversible migration commit boundary, and durably
claim that boundary before calling PostgreSQL. The fingerprint covers target
SHA, starting migration-ledger digest, and ordered migration identity/checksum
and compatibility data. Once claimed, the decision may be `executing` or
`uncertain`, and revocation is forbidden until the executor reconciles the
actual application ledger. Only an `approved` decision can be revoked;
executing, uncertain, no-commit, partial-commit, and committed decisions stay
immutable evidence. A revoked approval stays historical, while a later
permitted approval creates a fresh decision.

The executor must record a verified resulting application-ledger fingerprint
and the number of plan migrations that committed. Zero, partial, and complete
are distinct durable outcomes. A partial plan result fails the associated
deployment run but never pretends to undo already-applied migrations; that
exact plan cannot be reapproved, while a later corrective release can reconcile
forward from the real ledger.

Deployment-run failures are recorded as a bounded JSON object of safe,
operator-facing fields, such as `{"code":"BUILD_FAILED","message":"safe failure"}`.
Callers must not supply credentials, tokens, connection strings, raw command
lines, or other secrets in those details. Audit event details use the same
canonical JSON representation.
