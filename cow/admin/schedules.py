"""C3 Admin schedule/operational duty management, DENY-aware RBAC."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ninja import Query, Schema
from pydantic import ConfigDict, Field

from cow.common.driver_duty_api import (DutyEnvelope, DutyView, ScheduleEnvelope,
                                        SchedulePageEnvelope)
from cow.services import driver_duty as domain
from cow.services import recruitment_wire as wire


class Strict(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateSchedule(Strict):
    driver_id: UUID
    vehicle_id: UUID
    charger_id: UUID
    starts_at: datetime
    ends_at: datetime
    notes: str = Field(default="", max_length=500)


class AmendSchedule(Strict):
    reason: str = Field(min_length=8, max_length=500)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    vehicle_id: UUID | None = None
    charger_id: UUID | None = None


class ReasonInput(Strict):
    reason: str = Field(min_length=8, max_length=500)


class DutyPage(Strict):
    items: list[DutyView]


class DutyPageEnvelope(Strict):
    data: DutyPage


def register(router):
    @router.get("/schedules", operation_id="adminSchedulesList", response=SchedulePageEnvelope)
    def schedules(request, limit: int = Query(50, ge=1, le=100)):
        return {"data": domain.list_admin_schedules(request.auth, limit=limit)}

    @router.post("/schedules", operation_id="adminSchedulesCreate",
                 response={201: ScheduleEnvelope})
    def create(request, payload: CreateSchedule):
        return wire.mutate(
            request, "admin:schedules:create", payload,
            lambda: domain.create_schedule(request.auth, payload.model_dump()), status=201,
        )

    @router.get("/schedules/{schedule_id}", operation_id="adminSchedulesGet",
                response=ScheduleEnvelope)
    def get(request, schedule_id: UUID):
        return {"data": domain.get_admin_schedule(request.auth, schedule_id)}

    @router.patch("/schedules/{schedule_id}", operation_id="adminSchedulesAmend",
                  response={200: ScheduleEnvelope})
    def amend(request, schedule_id: UUID, payload: AmendSchedule):
        expected = wire.version(request)
        return wire.mutate(
            request, f"admin:schedules:amend:{schedule_id}", payload,
            lambda: domain.amend_schedule(
                request.auth, schedule_id, expected,
                payload.model_dump(exclude_none=True),
            ), expected_version=expected,
        )

    @router.post("/schedules/{schedule_id}/cancel", operation_id="adminSchedulesCancel",
                 response={200: ScheduleEnvelope})
    def cancel(request, schedule_id: UUID, payload: ReasonInput):
        expected = wire.version(request)
        return wire.mutate(
            request, f"admin:schedules:cancel:{schedule_id}", payload,
            lambda: domain.cancel_schedule(
                request.auth, schedule_id, expected, payload.reason,
            ), expected_version=expected,
        )

    @router.get("/duty-sessions", operation_id="adminDutySessionsList",
                response=DutyPageEnvelope)
    def duties(request, limit: int = Query(50, ge=1, le=100)):
        return {"data": domain.admin_duties(request.auth, limit=limit)}

    @router.get("/duty-sessions/{duty_id}", operation_id="adminDutySessionsGet",
                response=DutyEnvelope)
    def duty(request, duty_id: UUID):
        return {"data": domain.admin_duty(request.auth, duty_id)}

    @router.post("/duty-sessions/{duty_id}/hold", operation_id="adminDutyHold",
                 response={200: DutyEnvelope})
    def hold(request, duty_id: UUID, payload: ReasonInput):
        expected = wire.version(request)
        return wire.mutate(
            request, f"admin:duty:hold:{duty_id}", payload,
            lambda: domain.admin_hold(request.auth, duty_id, expected, payload.reason),
            expected_version=expected,
        )

    @router.post("/duty-sessions/{duty_id}/release-hold",
                 operation_id="adminDutyReleaseHold", response={200: DutyEnvelope})
    def release_hold(request, duty_id: UUID, payload: ReasonInput):
        expected = wire.version(request)
        return wire.mutate(
            request, f"admin:duty:release-hold:{duty_id}", payload,
            lambda: domain.admin_hold(
                request.auth, duty_id, expected, payload.reason, release=True,
            ), expected_version=expected,
        )
