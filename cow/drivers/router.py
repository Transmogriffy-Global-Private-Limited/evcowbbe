"""One driver route entrypoint, mounted by cow.api.

The existing driver auth/OTP routes are registered here. Driver-specific
business routes belong here, but must reuse shared core services and models.
"""
from ninja import Router
from cow.drivers.application import register as register_driver_applications


def build_router(auth_backend, mount_identity, mount_otp) -> Router:
    router = Router(auth=auth_backend, tags=["Driver"])
    mount_identity(router, "driver")
    register_driver_applications(router)
    mount_otp(router, "driver")
    return router
