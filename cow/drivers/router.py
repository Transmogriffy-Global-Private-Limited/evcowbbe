"""One driver route entrypoint, mounted by cow.api.

The existing driver auth/OTP routes are registered here. Driver-specific
business routes belong here, but must reuse shared core services and models.
"""
from ninja import Router
from cow.drivers.application import register as register_driver_applications
from cow.drivers.operational import register as register_driver_operational
from cow.drivers.duty import register as register_driver_duty


def build_router(auth_backend, mount_identity, mount_otp) -> Router:
    router = Router(auth=auth_backend, tags=["Driver"])
    mount_identity(router, "driver")
    register_driver_applications(router)
    register_driver_operational(router)
    register_driver_duty(router)
    mount_otp(router, "driver")
    return router
