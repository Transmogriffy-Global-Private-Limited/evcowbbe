"""C3 Driver schedule and duty HTTP interface: no self-granted clearance."""
from __future__ import annotations

from uuid import UUID
from ninja import Query
from cow.common.driver_duty_api import (
    StartInput, ReasonInput, MismatchInput,
    SchedulePageEnvelope, ScheduleEnvelope, CurrentDutyEnvelope,
    DutyEnvelope, BreakEnvelope, EligibleAssetsEnvelope, MismatchEnvelope, Strict,
)
from cow.services import driver_duty as domain
from cow.services import recruitment_wire as wire


def register(router):
    @router.get("/schedules", operation_id="driverSchedulesList", response=SchedulePageEnvelope)
    def schedules(request, limit: int = Query(50, ge=1, le=100)):
        return {"data": domain.list_own_schedules(request.auth, limit=limit)}

    @router.get("/schedules/{occurrence_id}", operation_id="driverSchedulesGet",
                response=ScheduleEnvelope)
    def schedule(request, occurrence_id: UUID):
        return {"data": domain.get_own_schedule(request.auth, occurrence_id)}

    @router.get("/duty/current", operation_id="driverDutyCurrent",
                response=CurrentDutyEnvelope)
    def current(request):
        return {"data": domain.current_duty(request.auth)}

    @router.get("/duty/current/eligible-assets", operation_id="driverDutyEligibleAssets",
                response=EligibleAssetsEnvelope)
    def eligible_assets(request):
        return {"data": domain.eligible_assets(request.auth)}

    @router.post("/duty/start", operation_id="driverDutyStart",
                 response={201: DutyEnvelope})
    def start(request, payload: StartInput):
        return wire.mutate(
            request, "driver:duty:start", payload,
            lambda: domain.start_duty(request.auth, payload.schedule_id), status=201,
        )

    @router.post("/duty/current/breaks", operation_id="driverDutyBreakStart",
                 response={201: BreakEnvelope})
    def begin_break(request, payload: ReasonInput):
        expected = wire.version(request)
        return wire.mutate(
            request, "driver:duty:break:start", payload,
            lambda: domain.start_break(request.auth, expected, payload.reason),
            expected_version=expected, status=201,
        )

    @router.post("/duty/current/breaks/{break_id}/end", operation_id="driverDutyBreakEnd",
                 response={200: DutyEnvelope})
    def finish_break(request, break_id: UUID, payload: Strict):
        expected = wire.version(request)
        return wire.mutate(
            request, f"driver:duty:break:end:{break_id}", payload,
            lambda: domain.end_break(request.auth, break_id, expected),
            expected_version=expected,
        )

    @router.post("/duty/current/end", operation_id="driverDutyEnd",
                 response={200: DutyEnvelope})
    def finish_duty(request, payload: ReasonInput):
        expected = wire.version(request)
        return wire.mutate(
            request, "driver:duty:end", payload,
            lambda: domain.end_duty(request.auth, expected, payload.reason),
            expected_version=expected,
        )

    @router.post("/duty/current/asset-mismatch", operation_id="driverDutyAssetMismatch",
                 response={201: MismatchEnvelope})
    def mismatch(request, payload: MismatchInput):
        expected = wire.version(request)
        return wire.mutate(
            request, "driver:duty:asset-mismatch", payload,
            lambda: domain.report_asset_mismatch(
                request.auth, expected, payload.observed_vehicle_id,
                payload.observed_charger_id, payload.reason,
            ), expected_version=expected, status=201,
        )
