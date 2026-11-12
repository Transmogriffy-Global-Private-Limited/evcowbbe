"""Self-service read-only Driver eligibility projection. No Driver self-clearance."""
from cow.services import driver_operational as domain
from cow.common.driver_operational_api import OperationalEnvelope


def register(router):
    @router.get("/operational", operation_id="driverOperationalRead",
                response=OperationalEnvelope)
    def get_operational(request):
        return {"data": domain.read_self(request.auth)}
