# Current EVCOWBBE VPS2 operations

`rehost-evcowbbe` is the **sole** Django-only deployment operator after guarded
adoption; `evcowbbe-cleanup.py` handles project-only, backed-up temporary test
cleanup. `launcher.py`, `mail_guard.py`, `mail_runner.py`, the service override
and mail timer/service are installed by `rehost-evcowbbe adopt` only after
verifying the original cutover topology and backing up old files.

Follow `docs/VPS2_CURRENT_OPERATIONS.md`. Do not install historical candidate
systemd templates or deploy Go to the canonical EVCOWBBE development hostname.
