"""Authenticates Go HAL webhooks and commits facts before acknowledging them.

No HAL callback may claim frontend/job state by itself without matching an exact
known charger, command and transaction. Unknown events fail closed.
"""
import json
import time
from datetime import datetime
from uuid import UUID
from django.conf import settings
from django.db import transaction, IntegrityError
from django.utils import timezone
from cow.errors import reject
from cow.hal.contract import verify, validate_fact, FactContractError, fingerprint
from cow.models import (HALFact, HALCommand, Charger, ChargingSession,
                        MeterObservation, ServiceJob, JobEvent)
from cow.services.jobs import transition_locked


def accept_signed_fact(body: bytes, epoch_text: str, signature: str):
    try:
        epoch = int(epoch_text)
    except (TypeError, ValueError):
        reject(401,"HAL_AUTH_INVALID","Invalid HAL signature metadata")
    if not verify(settings.COW_HAL_SHARED_SECRET,epoch,body,signature,int(time.time())):
        reject(401,"HAL_AUTH_INVALID","Invalid HAL signature")
    try:
        payload = json.loads(body)
        fact = validate_fact(payload)
    except (json.JSONDecodeError,FactContractError,ValueError,TypeError):
        reject(422,"HAL_FACT_INVALID","Unsupported HAL evidence")
    return ingest_fact(fact)


def ingest_fact(fact):
    dig = fingerprint(fact)
    event_id = UUID(fact["event_id"])
    with transaction.atomic():
        # Advisory lock serializes same event ID, allowing integrity conflict distinction.
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)",[int.from_bytes(event_id.bytes[:8],"big",signed=True)])
        old = HALFact.objects.filter(event_id=event_id).first()
        if old:
            if old.payload_digest != dig:
                reject(409,"HAL_EVENT_ID_CONFLICT","Event identity payload mismatch")
            return {"event_id":str(event_id),"outcome":"DUPLICATE"}
        charger = Charger.objects.filter(hal_identity=fact["charger_identity"]).first()
        if not charger:
            reject(422,"HAL_CHARGER_UNKNOWN","Unregistered HAL charger")
        kind, data = fact["kind"], fact["data"]
        command = None
        if kind.startswith("Command") or kind == "TransactionStarted":
            command = HALCommand.objects.select_for_update().filter(id=data["command_id"],charger=charger).first()
            if not command:
                reject(422,"HAL_COMMAND_UNKNOWN","Unknown operation identity")
        # Validate domain prerequisites before persisting event as accepted.
        session = None
        if kind == "TransactionStarted":
            if command.action != "StartCharging":
                reject(409,"HAL_COMMAND_MISMATCH","Start evidence does not match command")
            if ChargingSession.objects.filter(charger=charger,hal_transaction_id=data["transaction_id"]).exists():
                reject(409,"HAL_TRANSACTION_CONFLICT","Physical transaction already known")
            job = ServiceJob.objects.select_for_update().get(pk=command.job_id)
            if job.state != "SERVICE_PREPARING":
                reject(409,"JOB_STATE_CONFLICT","Cannot accept physical start for current job")
        elif kind in ("MeterSampleObserved","TransactionEnded"):
            session = ChargingSession.objects.select_for_update().filter(charger=charger,hal_transaction_id=data["transaction_id"]).first()
            if session is None:
                # Keep signed raw evidence durable and defer application until start is known.
                pass
            elif kind == "TransactionEnded" and session.state == "ENDED":
                reject(409,"HAL_TRANSACTION_ALREADY_ENDED","Already ended")
            if session and kind == "MeterSampleObserved" and session.state != "ACTIVE":
                # Physical evidence is still retained; a late meter may be useful for billing reconciliation.
                pass
        stored = HALFact.objects.create(event_id=event_id, kind=kind,
            contract_version=fact["contract"],payload_digest=dig,charger=charger,
            emitted_at=datetime.fromisoformat(fact["emitted_at"].replace("Z","+00:00")),body=data)
        if kind == "CommandAccepted":
            command.state="HAL_ACCEPTED"
            command.outcome_at=timezone.now()
            command.save(update_fields=["state","outcome_at","updated_at"])
        elif kind == "CommandRejected":
            command.state="REJECTED"
            command.outcome_at=timezone.now()
            command.save(update_fields=["state","outcome_at","updated_at"])
        elif kind == "CommandOutcomeUnknown":
            command.state="OUTCOME_UNKNOWN"
            command.outcome_at=timezone.now()
            command.save(update_fields=["state","outcome_at","updated_at"])
        elif kind == "TransactionStarted":
            ChargingSession.objects.create(job=job,charger=charger,
                connector_number=data["connector_number"],hal_transaction_id=data["transaction_id"],
                started_at=stored.emitted_at,meter_start_wh=data.get("meter_start_wh"),
                last_meter_wh=data.get("meter_start_wh"))
            transition_locked(job,"CHARGING","HAL_TRANSACTION_STARTED",{"event_id":str(event_id)})
            # Deferred meter/end facts may precede the physical start callback in transport.
            deferred = HALFact.objects.filter(charger=charger,processing_state="PENDING",
                 kind__in=["MeterSampleObserved","TransactionEnded"],
                 body__transaction_id=data["transaction_id"]).order_by("emitted_at","id")
            for pending in deferred:
                _apply_session_fact(pending,ChargingSession.objects.select_for_update().get(charger=charger,hal_transaction_id=data["transaction_id"]))
        elif kind in ("MeterSampleObserved","TransactionEnded"):
            if session:
                _apply_session_fact(stored,session)
        if kind not in ("MeterSampleObserved","TransactionEnded"):
            stored.processing_state="APPLIED"
            stored.applied_at=timezone.now()
            stored.save(update_fields=["processing_state","applied_at"])
        return {"event_id":str(event_id),"outcome":"DEFERRED" if stored.processing_state=="PENDING" else "RECORDED"}


def _apply_session_fact(stored,session):
    """Run inside caller transaction with session row lock."""
    data=stored.body
    if stored.kind=="MeterSampleObserved":
        energy=data["energy_wh"]
        MeterObservation.objects.get_or_create(session=session,fact=stored,
                                                defaults={"energy_wh":energy,"observed_at":stored.emitted_at})
        if session.last_meter_wh is None or energy>session.last_meter_wh:
            session.last_meter_wh=energy
            session.save(update_fields=["last_meter_wh","updated_at"])
    elif stored.kind=="TransactionEnded":
        if session.state=="ENDED":
            # Store evidence but flag contradictory/duplicate end for reconciliation.
            stored.processing_state="CONFLICT"
            stored.save(update_fields=["processing_state"])
            return
        session.state="ENDED"
        session.ended_at=stored.emitted_at
        session.meter_end_wh=data.get("meter_end_wh")
        session.save(update_fields=["state","ended_at","meter_end_wh","updated_at"])
    stored.processing_state="APPLIED"
    stored.applied_at=timezone.now()
    stored.save(update_fields=["processing_state","applied_at"])
