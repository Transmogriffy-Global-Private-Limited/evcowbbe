"""One user/Customer route entrypoint, mounted by cow.api.

This is a feature module, not a second Django application. It inherits
PlaneBearer('app') and keeps all route registration in one place.
"""

from uuid import UUID

from ninja import Body, Query, Router

from cow.users import service
from cow.users.charging import register_charging_routes
from cow.users.master import register_customer_routes
from cow.users.schemas import (
    AddressEnvelope,
    JobCancelEnvelope,
    JobCancelPayload,
    JobDetailEnvelope,
    JobListEnvelope,
    JobLiveEnvelope,
    NotificationEnvelope,
    NotificationListEnvelope,
    NotificationPreferencesEnvelope,
    NotificationPreferencesPayload,
    NotificationUnreadCountEnvelope,
    PromoteAddressPayload,
    PromoteVehiclePayload,
    RatingCreatePayload,
    RatingEnvelope,
    RatingListEnvelope,
    RealtimeTicketEnvelope,
    RealtimeTicketPayload,
    ServiceRequestEnvelope,
    ServiceRequestListEnvelope,
    SupportReplyPayload,
    SupportTicketCreatePayload,
    SupportTicketEnvelope,
    SupportTicketListEnvelope,
    VehicleEnvelope,
    WalletEnvelope,
    WalletRechargeOrderEnvelope,
    WalletRechargeOrderPayload,
    WalletRechargeVerifyEnvelope,
    WalletRechargeVerifyPayload,
    WalletTransactionListEnvelope,
)

REQUIRED_BODY = Body(...)


def register_user_feature_routes(router: Router) -> None:
    """Register all extended customer feature endpoints."""

    # ========================================================================
    # Service Requests
    # ========================================================================
    @router.get(
        "/service-requests",
        response=ServiceRequestListEnvelope,
        operation_id="app_service_requests_list",
    )
    def list_service_requests(request, limit: int = Query(default=50, ge=1, le=100)):
        return {"data": service.list_service_requests(request.auth, limit=limit)}

    @router.get(
        "/service-requests/{request_id}",
        response=ServiceRequestEnvelope,
        operation_id="app_service_request_get",
    )
    def get_service_request(request, request_id: UUID):
        return {"data": service.get_service_request(request.auth, request_id)}

    # ========================================================================
    # Jobs
    # ========================================================================
    @router.get(
        "/jobs",
        response=JobListEnvelope,
        operation_id="app_jobs_list",
    )
    def list_jobs(request, limit: int = Query(default=50, ge=1, le=100), state: str | None = None):
        return {"data": service.list_jobs(request.auth, limit=limit, state=state)}

    @router.post(
        "/jobs/{job_id}/cancel",
        response=JobCancelEnvelope,
        operation_id="app_jobs_cancel",
    )
    def cancel_job(request, job_id: UUID, payload: JobCancelPayload = REQUIRED_BODY):
        service.ensure_json(request)
        version = service.get_version_etag(request)
        key = service.get_idempotency_key(request, required=True)
        return service.cancel_job(request.auth, job_id, payload.model_dump(), version, key)

    @router.get(
        "/jobs/{job_id}/live",
        response=JobLiveEnvelope,
        operation_id="app_jobs_live",
    )
    def get_job_live(request, job_id: UUID):
        return {"data": service.get_job_live(request.auth, job_id)}

    @router.post(
        "/jobs/{job_id}/vehicle/promote",
        response=VehicleEnvelope,
        operation_id="app_jobs_promote_vehicle",
    )
    def promote_vehicle(request, job_id: UUID, payload: PromoteVehiclePayload = REQUIRED_BODY):
        service.ensure_json(request)
        key = service.get_idempotency_key(request, required=False)
        return service.promote_vehicle(request.auth, job_id, payload.model_dump(), key)

    @router.post(
        "/jobs/{job_id}/address/promote",
        response=AddressEnvelope,
        operation_id="app_jobs_promote_address",
    )
    def promote_address(request, job_id: UUID, payload: PromoteAddressPayload = REQUIRED_BODY):
        service.ensure_json(request)
        key = service.get_idempotency_key(request, required=False)
        return service.promote_address(request.auth, job_id, payload.model_dump(), key)

    # ========================================================================
    # Ratings
    # ========================================================================
    @router.get(
        "/ratings",
        response=RatingListEnvelope,
        operation_id="app_ratings_list",
    )
    def list_ratings(request, limit: int = Query(default=50, ge=1, le=100)):
        return {"data": service.list_ratings(request.auth, limit=limit)}

    @router.get(
        "/jobs/{job_id}/rating",
        response=RatingEnvelope,
        operation_id="app_jobs_rating_get",
    )
    def get_job_rating(request, job_id: UUID):
        return {"data": service.get_job_rating(request.auth, job_id)}

    @router.put(
        "/jobs/{job_id}/rating",
        response=RatingEnvelope,
        operation_id="app_jobs_rating_put",
    )
    def put_job_rating(request, job_id: UUID, payload: RatingCreatePayload = REQUIRED_BODY):
        service.ensure_json(request)
        key = service.get_idempotency_key(request, required=True)
        version = None
        if "If-Match" in request.headers:
            version = service.get_version_etag(request)
        return service.upsert_job_rating(request.auth, job_id, payload.model_dump(), version, key)

    # ========================================================================
    # Customer Support Tickets
    # ========================================================================
    @router.get(
        "/support/tickets",
        response=SupportTicketListEnvelope,
        operation_id="app_support_tickets_list",
    )
    def list_support_tickets(request, limit: int = Query(default=50, ge=1, le=100)):
        return {"data": service.list_support_tickets(request.auth, limit=limit)}

    @router.post(
        "/support/tickets",
        response=SupportTicketEnvelope,
        operation_id="app_support_tickets_create",
    )
    def create_support_ticket(request, payload: SupportTicketCreatePayload = REQUIRED_BODY):
        service.ensure_json(request)
        key = service.get_idempotency_key(request, required=False)
        return service.create_support_ticket(request.auth, payload.model_dump(), key)

    @router.get(
        "/support/tickets/{ticket_id}",
        response=SupportTicketEnvelope,
        operation_id="app_support_tickets_get",
    )
    def get_support_ticket(request, ticket_id: UUID):
        return {"data": service.get_support_ticket(request.auth, ticket_id)}

    @router.post(
        "/support/tickets/{ticket_id}/replies",
        response=SupportTicketEnvelope,
        operation_id="app_support_tickets_reply",
    )
    def reply_support_ticket(request, ticket_id: UUID, payload: SupportReplyPayload = REQUIRED_BODY):
        service.ensure_json(request)
        key = service.get_idempotency_key(request, required=True)
        version = None
        if "If-Match" in request.headers:
            version = service.get_version_etag(request)
        return service.reply_support_ticket(request.auth, ticket_id, payload.model_dump(), version, key)

    # ========================================================================
    # Notifications & Preferences
    # ========================================================================
    @router.get(
        "/notifications",
        response=NotificationListEnvelope,
        operation_id="app_notifications_list",
    )
    def list_notifications(request, limit: int = Query(default=50, ge=1, le=100)):
        return {"data": service.list_notifications(request.auth, limit=limit)}

    @router.get(
        "/notifications/unread-count",
        response=NotificationUnreadCountEnvelope,
        operation_id="app_notifications_unread_count",
    )
    def get_unread_count(request):
        return {"data": service.get_unread_notification_count(request.auth)}

    @router.post(
        "/notifications/{notification_id}/read",
        response=NotificationEnvelope,
        operation_id="app_notifications_read",
    )
    def mark_notification_read(request, notification_id: UUID):
        return {"data": service.mark_notification_read(request.auth, notification_id)}

    @router.post(
        "/notifications/read-all",
        response=NotificationListEnvelope,
        operation_id="app_notifications_read_all",
    )
    def mark_all_read(request):
        return {"data": service.mark_all_notifications_read(request.auth)}

    @router.get(
        "/notifications/preferences",
        response=NotificationPreferencesEnvelope,
        operation_id="app_notifications_preferences_get",
    )
    def get_preferences(request):
        return {"data": service.get_notification_preferences(request.auth)}

    @router.put(
        "/notifications/preferences",
        response=NotificationPreferencesEnvelope,
        operation_id="app_notifications_preferences_put",
    )
    def update_preferences(request, payload: NotificationPreferencesPayload = REQUIRED_BODY):
        service.ensure_json(request)
        return {"data": service.update_notification_preferences(request.auth, payload.model_dump())}

    # ========================================================================
    # Realtime SSE Tickets
    # ========================================================================
    @router.post(
        "/realtime/tickets",
        response=RealtimeTicketEnvelope,
        operation_id="app_realtime_tickets_create",
    )
    def create_realtime_ticket(request, payload: RealtimeTicketPayload = REQUIRED_BODY):
        service.ensure_json(request)
        return {"data": service.create_realtime_ticket(request.auth, payload.model_dump())}

    # ========================================================================
    # Wallet & Razorpay Payment Recharge
    # ========================================================================
    @router.get(
        "/wallet",
        response=WalletEnvelope,
        operation_id="app_wallet_get",
    )
    def get_wallet(request):
        data, version = service.get_wallet(request.auth)
        return service.as_response(200, data, etag=version)

    @router.get(
        "/wallet/transactions",
        response=WalletTransactionListEnvelope,
        operation_id="app_wallet_transactions_list",
    )
    def list_wallet_transactions(request, limit: int = Query(default=50, ge=1, le=100)):
        return {"data": service.list_wallet_transactions(request.auth, limit=limit)}

    @router.post(
        "/wallet/recharge/order",
        response={201: WalletRechargeOrderEnvelope},
        operation_id="app_wallet_recharge_order",
    )
    def create_recharge_order(request, payload: WalletRechargeOrderPayload = REQUIRED_BODY):
        service.ensure_json(request)
        key = service.get_idempotency_key(request, required=True)
        return service.create_wallet_recharge_order(request.auth, payload.model_dump(), key)

    @router.post(
        "/wallet/payment_order",
        response={201: WalletRechargeOrderEnvelope},
        operation_id="app_wallet_payment_order",
    )
    def create_payment_order_alias(request, payload: WalletRechargeOrderPayload = REQUIRED_BODY):
        service.ensure_json(request)
        key = service.get_idempotency_key(request, required=True)
        return service.create_wallet_recharge_order(request.auth, payload.model_dump(), key)

    @router.post(
        "/wallet/recharge/verify",
        response=WalletRechargeVerifyEnvelope,
        operation_id="app_wallet_recharge_verify",
    )
    def verify_recharge(request, payload: WalletRechargeVerifyPayload = REQUIRED_BODY):
        service.ensure_json(request)
        return {"data": service.verify_wallet_recharge(request.auth, payload.model_dump())}

    @router.post(
        "/wallet/verify_payment",
        response=WalletRechargeVerifyEnvelope,
        operation_id="app_wallet_verify_payment",
    )
    def verify_payment_alias(request, payload: WalletRechargeVerifyPayload = REQUIRED_BODY):
        service.ensure_json(request)
        return {"data": service.verify_wallet_recharge(request.auth, payload.model_dump())}



def build_router(auth_backend, mount_identity, mount_otp) -> Router:
    router = Router(auth=auth_backend, tags=["Customer"])
    mount_identity(router, "app")
    register_customer_routes(router)
    register_charging_routes(router)
    register_user_feature_routes(router)
    mount_otp(router, "app")
    return router
