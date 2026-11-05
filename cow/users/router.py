"""One user/Customer route entrypoint, mounted by cow.api.

This is a feature module, not a second Django application. It inherits
PlaneBearer('app') and keeps all route registration in one place.
"""
from ninja import Router

from cow.users.charging import register_charging_routes
from cow.users.master import register_customer_routes


def build_router(auth_backend, mount_identity, mount_otp) -> Router:
    router = Router(auth=auth_backend, tags=["Customer"])
    mount_identity(router, "app")
    register_customer_routes(router)
    register_charging_routes(router)
    mount_otp(router, "app")
    return router
