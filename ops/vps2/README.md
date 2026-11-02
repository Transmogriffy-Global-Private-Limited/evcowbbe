# Current EVCOWBBE VPS2 operations

`rehost-evcowbbe` is the **sole** Django-only deployment operator after guarded
adoption; `evcowbbe-cleanup.py` handles project-only, backed-up temporary test
cleanup. `launcher.py`, `mail_guard.py`, `mail_runner.py`, the service override
and mail timer/service are installed by `rehost-evcowbbe adopt` only after
verifying the original cutover topology and backing up old files.

Follow `docs/VPS2_CURRENT_OPERATIONS.md`. Do not install historical candidate
systemd templates or deploy Go to the canonical EVCOWBBE development hostname.

## Release marker service-account access

The `REVISION` file contains only the exact 40-hex Git revision and is intentionally
readable by the non-root `evcowbbe` account (`0444`). The canonical operator
verifies the release seal as root, then normalizes only this non-secret marker
and checks access **as `evcowbbe` before a service restart**.
`READY`, `MANIFEST.sha256`, secrets, credentials, Python virtualenv and source
retain their independent permissions. This prevents the cutover-era sealed
release (created with a private umask) from causing a launcher `PermissionError`.

An adoption failure must not be considered recovered from its backup message
alone: check `ORIGINAL_RUNTIME_HEALTH_RESTORED=PASS` and public HTTP readiness.
