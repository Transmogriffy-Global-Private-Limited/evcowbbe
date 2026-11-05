"""The single Admin role registration entrypoint for the central API.

Fleet retains its independently-authenticated router, registered separately by
cow.api, to preserve documented security/operation-ID behavior.
"""
from ninja import Router

from cow.admin.controls import register as register_admin_controls
from cow.admin.jobs import register_job_routes


def build_router(auth_backend, mount_identity, mount_otp) -> Router:
    router = Router(auth=auth_backend, tags=["Admin"])
    mount_identity(router, "admin")
    register_job_routes(router)
    register_admin_controls(router)
    mount_otp(router, "admin")
    return router
