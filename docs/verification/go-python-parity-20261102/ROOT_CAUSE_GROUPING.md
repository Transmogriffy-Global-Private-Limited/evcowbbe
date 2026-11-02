# Continuation root-cause reconciliation

This document reconciles two distinct baselines without treating aggregate
counts as a substitute for individual evidence.

## Report identities and counting rules

| Artifact | Checker | Finding definition | Count |
| --- | --- | --- | ---: |
| `../go_python_contract_diff.json` | historical V2 | request plus success-response declaration differences | 121 |
| Previous handback statement | `2026-11-02.1` | expanded request/response/parameter/error/security/header comparison | 786 |
| `PARITY_REPORT.json` | `2026-11-02.1` | same expanded checker, preserved checkpoint | 735 |
| `PARITY_REPORT_CONTINUATION_20261102.json` | `2026-11-02.1` | same expanded checker | 729 |
| `PARITY_REPORT_CONTINUATION_20261102_FINAL.json` | `2026-11-02.2` | same expanded checker | 723 |

The 786 result is recorded in the prior handback, but its machine-readable
per-finding artifact was overwritten before this continuation and is not
recoverable from the worktree. It is therefore not possible to invent a
row-by-row 786 mapping. The immutable 121 baseline is fully reconciled in
`RESOLUTION_LEDGER.csv`; every presently observable finding is individually
retained in `CURRENT_DISCREPANCY_LEDGER.csv`. The three report files above are
now retained as distinct checkpoints.

The 735 -> 729 -> 723 reductions use the same comparison algorithm. Version
`.2` only identifies the final checkpoint; it does not suppress a finding
class. The fall from 729 to 723 is the six logout request-body-required
findings removed after source inspection. The remaining logout schema
differences are retained and classified as a stale Go YAML declaration because
the Go handler's `post()` function validates only `Content-Type` and does not
decode a logout body.

## Historical 121 disposition

`RESOLUTION_LEDGER.csv` has all 121 immutable source rows and current matching
IDs where an exact finding still exists.

| Disposition | Count | Meaning |
| --- | ---: | --- |
| `PARTIALLY_RESOLVED_STATIC` | 73 | The exact operation/type/status mismatch is absent from the current report. This is static reconciliation only, not a claim of dynamic equivalence. |
| `UNCHANGED_OR_RECLASSIFIED` | 48 | An exact current finding remains, now with source-aware classification and a required next proof. |

The earlier `0/121 resolved` claim came from a defective ledger rule that
treated every historical request-schema row as unresolved and did not credit a
historical row whose exact key disappeared. It was an accounting defect. This
continuation does not overcorrect it: **0/121 are dynamically verified as fully
resolved**, while **73/121 are statically reconciled**.

## Current 723 root causes

`CURRENT_DISCREPANCY_LEDGER.csv` gives every finding an ID, operation, type,
status, full compared objects, classification, and evidence statement.

| Root cause / classification | Count | Required correction or proof |
| --- | ---: | --- |
| Python error/success/request declaration deficiency | 683 | Declare concrete status-specific error envelopes and reusable success DTOs, then exercise the matching handler. Most of the 646 error-response findings are omitted generated declarations, not proof of a runtime error. |
| Header/parameter declaration discrepancy | 23 | Compare `If-Match`, idempotency, and other raw request handling with the pinned handler, then declare or correct the endpoint. |
| Stale Go specification | 11 | Retain source-backed Django behavior: 43-byte opaque input accepts any alphabet; missing tokens reach domain errors; logout bodies are ignored despite YAML object text. |
| Stale Go specification or Python declaration discrepancy | 6 | Resolve nullable fleet catalog properties from `fleet.ValidateFields` per property, rather than erasing nullability. |

The report type counts are 646 `error_response_mismatch`, 36
`success_response_schema_mismatch`, 23 `parameter_mismatch`, and 18
`request_schema_mismatch`. There are no blanket exclusions and no unresolved
catch-all findings in the final current ledger.

## Shared corrections applied in this continuation

- Response header reference resolution in the checker and public OpenAPI
  declaration of the middleware-owned `X-Request-ID` header.
- Shared concrete acknowledgement DTOs for auth/RBAC operations and constrained
  roles/override decisions.
- Source-faithful opaque-token and password boundary handling, including
  omitted action/refresh values reaching domain errors instead of generic schema
  errors.
- Source-faithful logout behavior: bearer authentication and JSON media type
  are required, but body bytes are not decoded or schema-validated.
- Checker fixtures that prove both semantic equivalence and deliberate
  mismatches for refs, nullable representations, headers, parameters, security,
  and constraints.

## Static gate conclusion

The remaining 723 findings are substantive until each declaration is reconciled
against the pinned handler and isolated HTTP behavior. Therefore static schema
parity remains **FAIL**. This classification is a work queue, not a waiver.

## Shared-contract continuation: final checkpoint

The original arithmetic is corrected: `646 + 36 = 682` response-related
findings; with 23 parameter and 18 request findings, the preserved baseline
was 723. It was copied before work to
`checkpoint-723-before-shared-error-20261102/` with SHA-256 hashes.

After source inspection and shared implementation work, the unique final
artifact is `PARITY_REPORT_SHARED_CONTRACTS_FINAL_20261102.json`:

| Category | 723 baseline | Final | Change |
| --- | ---: | ---: | ---: |
| Error response declarations | 646 | 0 | -646 |
| Success response schemas | 36 | 30 | -6 |
| Parameters | 23 | 1 | -22 |
| Request schemas | 18 | 7 | -11 |
| Total | 723 | 38 | -685 |

Final report SHA-256:
`95297B80D6AB6EA11A95D150E4BA49D4EA74940781D31B52C3287BAE764FA4A5`.
Its report-specific current ledger SHA-256 is
`3DFC6A7C5D9F9DC27CDE56735AE8766059309B29310178997AA269B2680114A3`, and
its machine-readable root-cause map SHA-256 is
`FD94CA608C63924CA4D79B0ABF991C632D5D649673AC1CCF1DEA3B2ABD1689FB`.

### Fixed shared causes

- All 646 error rows were missing declarations of one source-verified shared
  boundary. `ErrorEnvelope`/`ErrorData`, middleware status normalization,
  OpenAPI error-status augmentation, and a real `Retry-After: 60` response for
  Go-path 429s now describe the actual common envelope. The checker resolves
  stale Go component-header omissions only in pinned-runtime mode, with a
  fixture proving this normalization is not general.
- Absent and invalid bearer credentials now use Go's
  `401 INVALID_CREDENTIALS` and exact public message. This was an actual
  isolated HTTP defect, not merely an OpenAPI difference.
- Shared manual mutation headers (`Idempotency-Key`, `If-Match`) are exposed
  only where current Django helpers read and validate them. Go parameter refs
  are resolved before comparison; an explicit fixture proves reference/direct
  equivalence.
- Query `limit` constraints are enforced by Ninja as well as the existing
  handler checks. Optional query `T | None` is normalized only for a
  non-required query scalar-null encoding, which cannot express JSON null on
  the wire; the fixture covers that narrow equivalence.
- Diagnostics now document actual source behavior: live/ready constants and
  the pinned logical version. The readiness failure was corrected to include
  the required empty `details` object.

### Remaining genuine work

Thirty success-schema rows are fleet/pricing response DTO gaps. They cannot be
closed honestly by copying Go YAML: the Django handlers currently return raw
`JsonResponse`/generic dictionaries and need concrete serialization evidence.
The seven request rows are six fleet nullable/range semantic questions and the
strict pricing-draft declaration. The remaining parameter is the pricing
`status` enum; runtime accepts its omitted/empty default and validates the
three named states, while the generated schema does not express that union.
