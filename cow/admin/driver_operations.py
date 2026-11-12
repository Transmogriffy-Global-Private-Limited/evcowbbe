"""C2 typed Admin Driver operational lifecycle routes, never arbitrary PATCH."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ninja import Query, Schema
from pydantic import ConfigDict, Field

from cow.common.driver_operational_api import OperationalEnvelope
from cow.services import driver_operational as domain
from cow.services import recruitment_wire as wire


class Strict(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DecisionInput(Strict):
    reason: str = Field(min_length=8, max_length=500)


class DecisionItem(Strict):
    id: UUID
    action: str
    from_status: str
    to_status: str
    reason: str
    actor_id: UUID
    version: int
    occurred_at: datetime


class DecisionPage(Strict):
    items: list[DecisionItem]
    next_before_at: datetime | None
    next_before_id: UUID | None


class DecisionPageEnvelope(Strict):
    data: DecisionPage


def register(router):
    @router.get("/drivers/{driver_id}/operational", operation_id="adminDriverOperationalGet",
                response=OperationalEnvelope)
    def get_operational(request, driver_id: UUID):
        return {"data": domain.read_admin(request.auth, driver_id)}

    @router.get("/drivers/{driver_id}/operational/decisions",
                operation_id="adminDriverOperationalDecisions",
                response=DecisionPageEnvelope)
    def get_decisions(request, driver_id: UUID,
                      limit: int = Query(50, ge=1, le=100),
                      before_at: datetime | None = None,
                      before_id: UUID | None = None):
        return {"data": domain.read_decisions(request.auth, driver_id, limit=limit,
                                               before_at=before_at, before_id=before_id)}

    # Each operation is a distinct endpoint with separately evaluated authority.
    # The initial PENDING -> ACTIVE path cannot be smuggled through reinstate.
    def add_action(action: str):
        @router.post(f"/drivers/{{driver_id}}/{action}",
                     operation_id="adminDriverOperational" + action.title(),
                     response={200: OperationalEnvelope})
        def apply(request, driver_id: UUID, payload: DecisionInput):
            expected = wire.version(request)
            return wire.mutate(
                request, f"admin:driver-operational:{driver_id}:{action}", payload,
                lambda: domain.decide(request.auth, driver_id, expected, action, payload.reason),
                expected_version=expected,
            )
    for action in ("clear", "suspend", "reinstate", "offboard"):
        add_action(action)
