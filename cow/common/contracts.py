"""Response DTOs and OpenAPI fragments shared by Django API routers."""

from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AckData(StrictSchema):
    ok: Literal[True]


class AckEnvelope(StrictSchema):
    data: AckData


class ErrorData(StrictSchema):
    """The fixed public failure payload emitted by the Go-compatible boundary."""

    code: str
    message: str
    request_id: UUID
    details: dict[str, Any]


class ErrorEnvelope(StrictSchema):
    error: ErrorData


# This is intentionally an inline OpenAPI fragment. Ninja only adds component
# models that appear in a route decorator, while errors are produced centrally
# by exception handlers and middleware. Keeping this fragment next to the DTO
# makes the published declaration match the serialized boundary without adding
# a fictitious endpoint-specific response model.
GO_ERROR_ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "request_id": {"type": "string", "format": "uuid"},
                "details": {"type": "object", "additionalProperties": True},
            },
            "required": ["code", "details", "message", "request_id"],
            "additionalProperties": False,
        }
    },
    "required": ["error"],
    "additionalProperties": False,
}
