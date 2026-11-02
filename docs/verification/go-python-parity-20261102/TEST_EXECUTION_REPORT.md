# Test execution report

Date: 2026-11-02. All commands ran at the repository root on the local review
worktree. The environment used `APP_ENV=test`, `DJANGO_SETTINGS_MODULE=config.settings`,
a deliberately unreachable local PostgreSQL URL (`127.0.0.1:1`), and non-secret
test-only pepper values. No database connection succeeded or was attempted as a
mutation.

## Repository preflight

| Command | Result |
| --- | --- |
| `git rev-parse --show-toplevel` | `C:/Users/AnubhabDey/Programs/My_Programs/evcowbbe` |
| `git branch --show-current` | `go-wire-parity-20261031` |
| `git rev-parse HEAD` | `760c64f17c3ecd196f95f6e344360ad22dc8c3a6` |
| `git diff --check` at start | Exit 0 |

The V2 modified/untracked files were present before this work and were retained.
No branch switch, reset, clean, stash, commit, push, deploy, VPS action, or
database mutation was performed.

## Executed checks

| Command | Exit | Evidence |
| --- | ---: | --- |
| `.venv\\Scripts\\python.exe -m unittest discover -v` without `DJANGO_SETTINGS_MODULE` | 1 | One existing direct-Django test import failed because settings were unset. This is recorded, not treated as a pass. |
| Same discovery command with `DJANGO_SETTINGS_MODULE=config.settings` | 0 | `Ran 78 tests ... OK`. |
| Final same discovery command after checker security self-test | 0 | `Ran 79 tests ... OK`. |
| `python manage.py check` | 0 | `System check identified no issues (0 silenced).` |
| `python manage.py makemigrations --check --dry-run --noinput` | 0 | `No changes detected`; Django emitted a connection-timeout warning while checking history because the deliberately unreachable database was used. It does not prove migration replay. |
| `python -m unittest tests.test_go_wire_contract tests.test_go_wire_checker tests.test_go_wire_http -v` | 0 | `Ran 16 tests ... OK`. Includes checker controlled-mismatch self-tests and isolated HTTP behavior fixtures. |
| `python scripts/check_go_wire_contract.py` | 1 | Expected fail-closed result; current output is `PARITY_REPORT.json`, with unresolved static differences. |
| `ruff check` over every modified Go-parity Python surface | 0 | All checks passed after scoped import/body-marker corrections. |
| `git diff --check` after edits | 0 | No whitespace errors. |

## Go reference tests

The current Python replacement checkout has no `go.mod`, so `go test ./...`
there fails before testing with `directory prefix . does not contain main
module`. The pinned Go source object is locally available and its handler
source was inspected through `git show`; an isolated Go source test extraction
was not completed in this session. Therefore **Go reference executable tests:
NOT RUN**, not PASS.

## Database and external services

No positively identified disposable PostgreSQL target and no authorization
marker/role credentials were available. PostgreSQL migration, persistence,
concurrency, and database-backed integration tests are **NOT RUN**. SMTP was
not contacted. No live Go service was contacted.

## Continuation execution

All continuation commands used `.venv\\Scripts\\python.exe`, `APP_ENV=test`,
the documented Django settings, test-only non-secret pepper values, and the
unreachable loopback database URL. No database mutation was possible or
attempted.

| Command | Exit | Evidence |
| --- | ---: | --- |
| `python -m unittest tests.test_go_wire_http -v` with the system interpreter | 1 | Environment precondition failure: that interpreter has no Django. No source assertion ran. |
| `.venv\\Scripts\\python.exe -m unittest tests.test_go_wire_http -v` | 0 | 6 isolated HTTP tests passed. |
| `.venv\\Scripts\\python.exe -m unittest tests.test_go_wire_checker tests.test_go_wire_contract tests.test_go_wire_http -v` | 0 | 20 tests passed, including deliberate checker mismatch/equivalence fixtures. |
| `.venv\\Scripts\\python.exe -m unittest discover -v` | 0 | 83 tests passed. |
| `.venv\\Scripts\\python.exe manage.py check` | 0 | No system-check issues. |
| `.venv\\Scripts\\python.exe manage.py makemigrations --check --dry-run --noinput` | 0 | No model drift. Django issued an expected connection-history timeout warning against `127.0.0.1:1`; this does not prove migration replay. |
| `.venv\\Scripts\\python.exe scripts\\check_go_wire_contract.py --output ...PARITY_REPORT_CONTINUATION_20261102_FINAL.json` | 1 | Expected fail-closed exit with 723 retained findings. |
| `.venv\\Scripts\\ruff.exe check` over all changed parity Python files | 0 | All checks passed. |

The system-interpreter error is retained for auditability and is not a test
failure attributable to the candidate; all substantive Python verification
used the repository virtual environment.

## Shared-contract continuation execution

| Command | Exit | Interpretation |
| --- | ---: | --- |
| `.venv\\Scripts\\python.exe -m unittest tests.test_go_wire_http tests.test_go_wire_checker tests.test_fleet_contract -v` | 0 | 25 focused tests passed after shared error/header/parameter corrections. |
| `.venv\\Scripts\\python.exe scripts\\check_go_wire_contract.py --output ...PARITY_REPORT_SHARED_CONTRACTS_FINAL_20261102.json` | 1 | Expected fail-closed result: 38 retained findings; unique report and report-specific ledgers/root-cause map written. |
| `.venv\\Scripts\\python.exe -m unittest discover -v` | 0 | 89 tests passed. |
| `.venv\\Scripts\\python.exe manage.py check` | 0 | No system-check issues. |
| `.venv\\Scripts\\python.exe manage.py makemigrations --check --dry-run --noinput` | 0 | No model drift; the deliberately unreachable database emitted a history-check timeout warning, not a replay pass. |
| `.venv\\Scripts\\ruff.exe check` over changed parity sources | 0 | All checks passed. |
| `git diff --check` | 0 | No whitespace errors. |
