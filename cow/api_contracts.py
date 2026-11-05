"""Historical shared-contract import shim.

New code must import from cow.common.contracts."""

from cow.common.contracts import (
    AckData as AckData,
    AckEnvelope as AckEnvelope,
    ErrorData as ErrorData,
    ErrorEnvelope as ErrorEnvelope,
    GO_ERROR_ENVELOPE_SCHEMA as GO_ERROR_ENVELOPE_SCHEMA,
    StrictSchema as StrictSchema,
)
