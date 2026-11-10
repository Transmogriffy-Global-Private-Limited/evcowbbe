"""A transport only. Delivery uncertainty is persisted, never auto-replayed."""
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone as django_tz
from cow.hal.contract import canonical, make_command, sign
from cow.models import HALCommand, AuditEvent
from cow.common.errors import DomainError
from cow.services import operational_eligibility as admission


def send_operation_once(command_id):
    """Process a single committed operation exactly once per deliberate invocation.

    Reconciliation is separate and must not assume a timeout means not executed.
    """
    if not settings.COW_HAL_BASE_URL or len(settings.COW_HAL_SHARED_SECRET) < 32:
        return "DISCONNECTED"
    with transaction.atomic():
        admission.lock_gate()
        command = HALCommand.objects.select_for_update().select_related("charger").get(pk=command_id)
        if command.state != "PENDING":
            return command.state
        if command.action == "StartCharging":
            try:
                admission.assert_start_admissible(
                    command.job_id, command.charger_id,
                    command.params["connector_number"], existing_command_id=command.pk,
                )
            except (DomainError, KeyError, TypeError, ValueError) as exc:
                # Never send a stale, unadmitted PENDING command. A rejected
                # pre-send command cannot have been delivered by this worker.
                code = exc.code if isinstance(exc, DomainError) else "INVALID_COMMAND_PARAMS"
                command.state = "REJECTED"
                command.outcome_at = django_tz.now()
                command.save(update_fields=["state", "outcome_at", "updated_at"])
                AuditEvent.objects.create(
                    actor=None, target_type="HALCommand", target_id=command.pk,
                    action="COMMAND_PRESEND_REJECTED", details={"reason_code": code},
                )
                return "REJECTED"
        command.state = "DELIVERY_ATTEMPTED"
        command.attempted_at = django_tz.now()
        command.save(update_fields=["state","attempted_at","updated_at"])
    body = canonical(make_command(command.id,command.charger.hal_identity,command.action,command.params))
    epoch = int(time.time())
    try:
        with httpx.Client(timeout=8,follow_redirects=False) as client:
            res = client.post(settings.COW_HAL_BASE_URL.rstrip("/")+"/v1/operations",
                content=body,headers={"Content-Type":"application/json", "X-HAL-Timestamp":str(epoch),
                        "X-HAL-Signature":sign(settings.COW_HAL_SHARED_SECRET,epoch,body)})
        if res.status_code == 202:
            state="HAL_ACCEPTED" # queue receipt, NOT physical action
        elif res.status_code in (400,422):
            state="REJECTED" # only if Go HAL contract guarantees no side effect for validation rejection
        else:
            state="OUTCOME_UNKNOWN"
    except (httpx.RequestError,ValueError):
        state="OUTCOME_UNKNOWN"
    with transaction.atomic():
        admission.lock_gate()
        # A signed HAL fact may have advanced the state during HTTP IO.
        # Never overwrite that fact-driven update.
        HALCommand.objects.filter(pk=command_id,state="DELIVERY_ATTEMPTED").update(state=state)
    return state
