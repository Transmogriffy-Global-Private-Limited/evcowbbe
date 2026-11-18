"""CoW-owned charging machine for the unchanged dedicated Go HAL v1.

SOURCE CANDIDATE ONLY: no migration, runtime, OCPP, or financial acceptance in this drop.
No direct public issuance of commercial approvals; no network call inside DB locks.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from cow.common.errors import DomainError, reject
from cow.hal.v1_charge_contract import (
    ChargeContractError, GO_COMMAND_STATES, canonical_uuid, ensure_id_chain, nonnegative,
    strict_positive, utc_time, valid_soc, validate_limits,
)
from cow.hal.v1_wire import WireError
from cow.hal import v1_machine
from cow.models import (
    AuditEvent, Charger, ChargerConnector, ChargingSession, HALCommand,
    HALV1ChargerOperation, HALV1ChargingCommand,
    HALV1ChargingIntent, HALV1ChargingPermit, HALV1ChargingSession,
    HALV1FactReceipt, HALV1Mapping, HALV1ChargerRuntime, HALV1ConnectorRuntime,
)
from cow.services import operational_eligibility as safety


def _cipher() -> Fernet:
    # Derive an independent Fernet purpose from the existing secure pepper.
    return Fernet(settings.COW_HAL_V1_CHARGE_CIPHER_KEY.encode('ascii'))


def _redact_audit(target, action, *, principal=None, **details):
    AuditEvent.objects.create(actor=principal,target_type='HALV1Charging',
                              target_id=target,action=action,details=details)


def _strtime(value):
    return value.isoformat().replace('+00:00', 'Z')


def _snapshot_mapping(permit, *, lock=False):
    charger_q = Charger.objects.select_for_update() if lock else Charger.objects
    charger = charger_q.filter(pk=permit.charger_id).first()
    connector_q = ChargerConnector.objects.select_for_update() if lock else ChargerConnector.objects
    connector = connector_q.filter(pk=permit.connector_id, charger_id=permit.charger_id).first()
    if (not charger or not charger.active or charger.operational_status!='AVAILABLE' or
        not connector or not connector.active):
        reject(409, 'HAL_CHARGE_CONNECTOR_UNAVAILABLE', 'Approved connector unavailable')
    mapping = HALV1Mapping.objects.select_for_update().filter(
        charger=charger, scope_id=permit.scope_id, state='SYNCHRONIZED').first()
    if not mapping:
        reject(409, 'HAL_CHARGE_MAPPING_NOT_READY', 'CoW HAL mapping unsynchronized')
    snapshot = v1_machine.mapping_payload(charger)
    if (not snapshot['enabled'] or snapshot != mapping.snapshot or
        not any(x['cms_connector_id'] == str(connector.pk) and
                x['ocpp_connector_number'] == connector.connector_number
                for x in snapshot['connectors'])):
        reject(409, 'HAL_CHARGE_MAPPING_DRIFT', 'Connector or charger mapping changed')
    return charger, connector, snapshot


def _permit_contract(permit, now):
    # This is an internal business-commit contract, NOT a user-supplied assertion.
    if (permit.state != 'RESERVED' or permit.expires_at <= now or
        permit.scope_id != UUID(v1_machine.scope()) or
        permit.owner.plane != 'app' or permit.owner.status != 'ACTIVE' or
        permit.channel not in {'ROAMING','FIXED_QR'} or
        not isinstance(permit.wallet_hold_reference,str) or
        len(permit.wallet_hold_reference) < 16):
        reject(409,'HAL_CHARGE_PERMIT_INVALID','Approved financial authorization required')
    commercial = permit.commercial_snapshot
    if (not isinstance(commercial,dict) or
        commercial.get('state') != 'COMMITTED' or
        commercial.get('hold_reference') != permit.wallet_hold_reference or
        not isinstance(commercial.get('tariff'),dict) or not commercial['tariff'] or
        not isinstance(commercial.get('tax'),dict) or not commercial['tax'] or
        commercial.get('wallet_hold_state') != 'HELD'):
        reject(409,'HAL_CHARGE_COMMERCIAL_NOT_COMMITTED',
               'A verifiable, committed tariff/tax and wallet hold is required')
    if (not isinstance(permit.location_evidence,dict) or
        permit.location_evidence.get('verified') is not True or
        not isinstance(permit.operational_evidence,dict) or
        permit.operational_evidence.get('verified') is not True):
        reject(409,'HAL_CHARGE_EVIDENCE_MISSING',
               'Trusted serviceability and operational evidence is required')
    if (permit.channel == 'ROAMING' and permit.job_id is None or
        permit.channel == 'FIXED_QR' and permit.job_id is not None):
        reject(409,'HAL_CHARGE_CHANNEL_INVALID','Channel and job mismatch')
    if commercial.get('source') == 'COW_BOOKING_COMMERCIAL_V2':
        from django.db.models import Sum
        from cow.models import (CoWBookingCommercialCommitment,
            CoWBookingChargingAllocation)
        from cow.services.charging_commercial_policy import stable_digest
        allocation=CoWBookingChargingAllocation.objects.select_for_update().filter(
            permit=permit).first()
        booking=CoWBookingCommercialCommitment.objects.select_for_update().filter(
            pk=commercial.get('booking_id'),owner_id=permit.owner_id,
            job_id=permit.job_id).first()
        if (allocation is None or booking is None or
            allocation.booking_id != booking.pk or
            booking.state != 'START_PENDING' or
            booking.snapshot_sha256 != stable_digest(booking.snapshot) or
            commercial.get('booking_digest') != booking.snapshot_sha256 or
            allocation.authorized_session_wh != permit.energy_limit_wh or
            allocation.budget_gross_paise != commercial.get('budget_gross_paise') or
            allocation.authorized_total_wh != commercial.get('authorized_cumulative_wh') or
            allocation.authorized_prior_wh != commercial.get('previous_completed_wh') or
            allocation.proof_sha256 != commercial.get('handover_sha256') or
            booking.protected_buffer_paise <= 0 or
            commercial.get('buffer_paise') != booking.protected_buffer_paise or
            permit.limit_type != 'AUTO' or permit.energy_limit_source != 'WALLET' or
            permit.max_duration_seconds != 0 or
            allocation.authorized_session_wh <= 0):
            reject(409,'BOOKING_PERMIT_AUTHORITY_INVALID',
                'Booking level commercial authorization is missing or changed')
        wallet=booking.wallet.__class__.objects.select_for_update().filter(
            pk=booking.wallet_id,customer_id=permit.owner_id,currency='INR').first()
        if wallet is None:
            reject(409,'BOOKING_WALLET_SCOPE_INVALID','Booking wallet missing')
        holds=(CoWBookingCommercialCommitment.objects.filter(wallet=wallet,
            state__in=('FUNDED','START_PENDING','IN_SERVICE','CLOSING','RECONCILIATION_REQUIRED'))
            .aggregate(total=Sum('reserved_paise'))['total'] or 0)
        if (holds > wallet.balance_paise or
            booking.reserved_paise < booking.fixed_fees_paise+
                booking.minimum_charging_paise+booking.protected_buffer_paise):
            reject(409,'BOOKING_WALLET_HOLD_BROKEN','Wallet commitment not funded')
    try:
        validate_limits(permit.limit_type,permit.energy_limit_wh,
                        permit.energy_limit_source,permit.max_duration_seconds,
                        permit.duration_limit_source)
    except ChargeContractError as exc:
        reject(409,exc.code,'Approved limits invalid')


def intent_view(intent):
    permit = intent.permit
    session=HALV1ChargingSession.objects.filter(intent=intent).first()
    return {
        'id':str(intent.pk),'state':intent.state,'channel':permit.channel,
        'charger_id':str(permit.charger_id),'connector_id':str(permit.connector_id),
        'session_id':str(session.pk) if session else None,
        'created_at':_strtime(intent.created_at),
    }


def _require_idempotency_key(key):
    if (type(key) is not str or not 16<=len(key)<=128 or
        any(ord(c)<32 or ord(c)==127 for c in key)):
        reject(428,'HAL_CHARGE_IDEMPOTENCY_REQUIRED','Safe bounded Idempotency-Key required')


def stage_start(owner, permit_id, idempotency_key):
    """Commit start intent/credential/command only from a trusted upstream permit.

    The permit must already have been issued by a separate atomic financial
    authority. A user cannot create it through the HAL API.
    """
    _require_idempotency_key(idempotency_key)
    if not getattr(settings,'COW_HAL_V1_NEW_STARTS_ENABLED',False):
        reject(409,'HAL_V1_NEW_STARTS_DISABLED','Charging admission is disabled')
    with transaction.atomic():
        safety.lock_gate()
        permit = HALV1ChargingPermit.objects.select_for_update().select_related(
            'owner','job').filter(pk=permit_id,owner=owner).first()
        if not permit:
            reject(404,'HAL_CHARGE_PERMIT_NOT_FOUND','No approved charging permit')
        existing=HALV1ChargingIntent.objects.filter(permit=permit).first()
        if existing:
            if existing.idempotency_key != idempotency_key:
                reject(409,'HAL_CHARGE_IDEMPOTENCY_CONFLICT','Permit already bound to another start request')
            return intent_view(existing)
        now=timezone.now()
        _permit_contract(permit,now)
        if permit.channel == 'ROAMING':
            # Exact C3/Stage-B, job and duty checks under the same global gate.
            from cow.services import operational_eligibility as admission
            admission.assert_start_admissible(permit.job_id,permit.charger_id,
                                              permit.connector.connector_number,
                                              allow_followup=(permit.commercial_snapshot.get('source') ==
                                                              'COW_BOOKING_COMMERCIAL_V2'))
            if permit.job.request.customer_id != owner.pk:
                reject(403,'HAL_CHARGE_CUSTOMER_MISMATCH','Customer does not own job')
        charger,connector,mapping=_snapshot_mapping(permit,lock=True)
        # Fixed QR and roaming must not overlap legacy or Admin HAL control.
        if (ChargingSession.objects.filter(
                charger=charger,connector_number=connector.connector_number,state='ACTIVE').exists() or
            HALCommand.objects.filter(charger=charger,state__in=safety.BUSY_COMMAND_STATES).exists() or
            HALV1ChargerOperation.objects.filter(charger=charger,state__in=(
                'PERSISTED','DELIVERY_ATTEMPTED','RECEIPT_RECORDED','RECONCILIATION_REQUIRED')).exists()):
            reject(409,'HAL_CHARGE_ASSET_BUSY','Charger has unresolved physical or control activity')
        if HALV1ChargingSession.objects.filter(connector=connector,state__in=(
            'ACTIVE','RECONCILIATION_REQUIRED')).exists():
            reject(409,'HAL_CHARGE_CONNECTOR_BUSY','Connector has unresolved charging')
        if HALV1ChargingIntent.objects.filter(permit__connector=connector,
            state__in=('PERSISTED','DELIVERY_ATTEMPTED','ACCEPTED_FOR_DELIVERY',
                       'PROTOCOL_ACKNOWLEDGED','RECONCILIATION_REQUIRED','ACTUALLY_STARTED')).exclude(
                state='ACTUALLY_STARTED',halv1chargingsession__state__in=(
                    'PHYSICALLY_COMPLETED','SETTLED')).exists():
            reject(409,'HAL_CHARGE_INTENT_BUSY','Connector has unresolved start intent')
        command_expiry=min(now+timedelta(minutes=6),permit.expires_at)
        credential_expiry=min(now+timedelta(minutes=5),command_expiry-timedelta(minutes=1))
        if credential_expiry <= now+timedelta(seconds=30):
            reject(409,'HAL_CHARGE_PERMIT_TOO_SHORT','Financial authorization window too short')
        tag='appv1_'+secrets.token_urlsafe(9)
        if len(tag)>20:
            raise RuntimeError('Credential generation exceeded OCPP idTag capacity')
        intent=HALV1ChargingIntent.objects.create(
            permit=permit,idempotency_key=idempotency_key,credential_ciphertext=_cipher().encrypt(tag.encode()).decode(),
            credential_sha256=hashlib.sha256(tag.encode()).hexdigest(),
            credential_expires_at=credential_expiry,command_expires_at=command_expiry,
            mapping_snapshot=mapping)
        cmd_id=uuid4();trace_id=uuid4();correlation=uuid4()
        HALV1ChargingCommand.objects.create(
            id=cmd_id,intent=intent,kind='START',idempotency_key=idempotency_key,trace_id=trace_id,
            correlation_id=correlation,snapshot={
                'trace_id':str(trace_id),'cms_command_id':str(cmd_id),
                'cms_start_intent_id':str(intent.pk),'cpo_id':v1_machine.scope(),
                'customer_id':str(owner.pk),'cms_charger_id':str(charger.pk),
                'cms_connector_id':str(connector.pk),
                'charger_ocpp_identity':mapping['charger_ocpp_identity'],
                'ocpp_connector_number':connector.connector_number,
                'credential_expires_at':_strtime(credential_expiry),
                'command_expires_at':_strtime(command_expiry),
                'limit_type':permit.limit_type,
                'energy_limit_wh':permit.energy_limit_wh,
                'energy_limit_source':permit.energy_limit_source,
                'max_duration_seconds':permit.max_duration_seconds,
                'duration_limit_source':permit.duration_limit_source,
            })
        permit.state='BOUND';permit.save(update_fields=['state','updated_at'])
        _redact_audit(intent.pk,'HAL_V1_START_INTENT_STAGED',principal=owner,channel=permit.channel,
                      charger_id=str(charger.pk),connector_id=str(connector.pk))
        return intent_view(intent)


def stage_stop(owner, session_id, idempotency_key, *, initiated_by='customer',reason='user_requested',actor=None):
    # Stop remains possible with NEW_STARTS_ENABLED=false. Never release money.
    _require_idempotency_key(idempotency_key)
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=100:
        reject(422,'HAL_CHARGE_STOP_REASON_INVALID','Stop reason invalid')
    if initiated_by not in {'customer','admin'} or (initiated_by=='customer' and owner is None):
        reject(422,'HAL_CHARGE_STOP_INITIATOR_INVALID','Stop initiator invalid')
    with transaction.atomic():
        if initiated_by=='admin':
            if actor is None:
                reject(403,'HAL_CHARGE_ADMIN_REQUIRED','Admin authorization required')
            safety.fresh_admin(actor,'charging.command')
        safety.lock_gate()
        session=HALV1ChargingSession.objects.select_for_update().select_related(
            'intent__permit__owner','intent__permit__charger','intent__permit__connector'
        ).filter(pk=session_id).first()
        if not session or (owner is not None and session.intent.permit.owner_id!=owner.pk):
            reject(404,'HAL_CHARGE_SESSION_NOT_FOUND','Session not found')
        existing=HALV1ChargingCommand.objects.select_for_update().filter(
            intent=session.intent,kind='STOP').first()
        if existing:
            if (existing.idempotency_key != idempotency_key or
                existing.snapshot.get('requested_stop_reason') != reason.strip() or
                existing.snapshot.get('requested_stop_initiator') != initiated_by):
                reject(409,'HAL_CHARGE_IDEMPOTENCY_CONFLICT','Stop request conflicts with prior command')
            return command_view(existing)
        if session.stopped_at is not None or session.state not in {'ACTIVE','RECONCILIATION_REQUIRED'}:
            reject(409,'HAL_CHARGE_NOT_ACTIVE','Physical session is already closed')
        permit=session.intent.permit
        # A physical stop must remain stageable after administrative mapping drift.
        # It targets the *frozen* command/start mapping, never a replacement gun.
        frozen=session.intent.mapping_snapshot
        if (not isinstance(frozen,dict) or
            frozen.get('cms_charger_id')!=str(permit.charger_id) or
            frozen.get('cpo_id')!=str(permit.scope_id) or
            not any(isinstance(row,dict) and
                    row.get('cms_connector_id')==str(permit.connector_id) and
                    type(row.get('ocpp_connector_number')) is int and
                    row['ocpp_connector_number']>0
                    for row in frozen.get('connectors',[])) or
            not isinstance(frozen.get('charger_ocpp_identity'),str) or
            not frozen['charger_ocpp_identity']):
            reject(409,'HAL_CHARGE_STOP_SNAPSHOT_INVALID',
                   'Frozen physical connector identity cannot be proven')
        now=timezone.now();cmd_id=uuid4();trace_id=uuid4()
        cmd=HALV1ChargingCommand.objects.create(
            id=cmd_id,intent=session.intent,session=session,kind='STOP',idempotency_key=idempotency_key,trace_id=trace_id,
            correlation_id=uuid4(),snapshot={
                'trace_id':str(trace_id),'cms_command_id':str(cmd_id),
                'cms_charging_session_id':str(session.pk),'cpo_id':v1_machine.scope(),
                'customer_id':str(permit.owner_id),'cms_charger_id':str(permit.charger_id),
                'cms_connector_id':str(permit.connector_id),
                'charger_ocpp_identity':frozen['charger_ocpp_identity'],
                'ocpp_connector_number':next(row['ocpp_connector_number'] for row in frozen['connectors']
                                             if row['cms_connector_id']==str(permit.connector_id)),
                'hal_transaction_id':str(session.hal_transaction_id),
                'ocpp_transaction_id':session.ocpp_transaction_id,
                'requested_stop_initiator':initiated_by,
                'requested_stop_reason':reason.strip(),
                'command_expires_at':_strtime(now+timedelta(minutes=2)),
            })
        _redact_audit(cmd.pk,'HAL_V1_STOP_STAGED',principal=actor if initiated_by=='admin' else owner,
                      session_id=str(session.pk),initiator=initiated_by)
        return command_view(cmd)


def command_view(cmd):
    return {
        'id':str(cmd.pk),'kind':cmd.kind,'state':cmd.state,
        'intent_id':str(cmd.intent_id),'session_id':str(cmd.session_id) if cmd.session_id else None,
        'hal_command_id':str(cmd.hal_command_id) if cmd.hal_command_id else None,
        'provider_state':cmd.provider_state,'failure_code':cmd.failure_code,
        'attempted_at':_strtime(cmd.attempted_at) if cmd.attempted_at else None,
    }


def send_once(command_id):
    """One outbound attempt. Durable attempt flag first, then network OUTSIDE tx."""
    with transaction.atomic():
        safety.lock_gate()
        cmd=HALV1ChargingCommand.objects.select_for_update().select_related(
            'intent__permit','intent__permit__charger','intent__permit__connector'
        ).filter(pk=command_id).first()
        if not cmd:reject(404,'HAL_CHARGE_COMMAND_NOT_FOUND','Charging command not found')
        if cmd.state!='PERSISTED':return command_view(cmd)
        if cmd.kind=='START':
            if not getattr(settings,'COW_HAL_V1_NEW_STARTS_ENABLED',False):
                reject(409,'HAL_V1_NEW_STARTS_DISABLED','New charging admission disabled')
            permit=cmd.intent.permit
            if permit.commercial_snapshot.get('source') == 'COW_BOOKING_COMMERCIAL_V2':
                # A permit may have been staged earlier. Before ANY network attempt,
                # reconfirm the exact held booking, even if its job still says CHARGING.
                from django.db.models import Sum
                from cow.models import CoWBookingCommercialCommitment, CoWBookingChargingAllocation
                booking = CoWBookingCommercialCommitment.objects.select_for_update().filter(
                    pk=permit.commercial_snapshot.get('booking_id'),
                    owner_id=permit.owner_id, job_id=permit.job_id).first()
                allocation = CoWBookingChargingAllocation.objects.select_for_update().filter(
                    permit=permit).first()
                wallet = (booking.wallet.__class__.objects.select_for_update().filter(
                    pk=booking.wallet_id, customer_id=permit.owner_id).first()
                    if booking is not None else None)
                all_held = (CoWBookingCommercialCommitment.objects.filter(wallet=wallet,
                    state__in=('FUNDED','START_PENDING','IN_SERVICE','CLOSING','RECONCILIATION_REQUIRED'))
                    .aggregate(total=Sum('reserved_paise'))['total'] or 0) if wallet is not None else 0
                valid = (booking is not None and allocation is not None and wallet is not None and
                    booking.state == 'START_PENDING' and
                    booking.snapshot_sha256 == permit.commercial_snapshot.get('booking_digest') and
                    allocation.authorized_session_wh == permit.energy_limit_wh and
                    allocation.state == 'PREPARED' and
                    wallet.currency == 'INR' and all_held <= wallet.balance_paise and
                    booking.reserved_paise >= booking.fixed_fees_paise +
                    booking.minimum_charging_paise + booking.protected_buffer_paise)
                if not valid:
                    cmd.state='RECONCILIATION_REQUIRED'
                    cmd.failure_code='BOOKING_WALLET_OR_STATE_DRIFT_BEFORE_SEND'
                    cmd.save(update_fields=['state','failure_code','updated_at'])
                    cmd.intent.state='RECONCILIATION_REQUIRED'
                    cmd.intent.save(update_fields=['state','updated_at'])
                    permit.state='RECONCILIATION_REQUIRED'
                    permit.save(update_fields=['state','updated_at'])
                    return command_view(cmd)
            if (permit.commercial_snapshot.get('source') == 'COW_BOOKING_COMMERCIAL_V2' and
                (permit.job is None or permit.job.state not in {'SERVICE_PREPARING','CHARGING'})):
                cmd.state='RECONCILIATION_REQUIRED'
                cmd.failure_code='BOOKING_CLOSED_BEFORE_START_SEND'
                cmd.save(update_fields=['state','failure_code','updated_at'])
                cmd.intent.state='RECONCILIATION_REQUIRED'
                cmd.intent.save(update_fields=['state','updated_at'])
                permit.state='RECONCILIATION_REQUIRED'
                permit.save(update_fields=['state','updated_at'])
                return command_view(cmd)
            if permit.state!='BOUND' or permit.expires_at<=timezone.now():
                cmd.state='EXPIRED';cmd.failure_code='HAL_PERMIT_NO_LONGER_VALID'
                cmd.outcome_at=timezone.now()
                cmd.save(update_fields=['state','failure_code','outcome_at','updated_at'])
                if cmd.intent.state=='PERSISTED':
                    cmd.intent.state='EXPIRED'
                    cmd.intent.save(update_fields=['state','updated_at'])
                if permit.state=='BOUND':
                    permit.state='RECONCILIATION_REQUIRED'
                    permit.save(update_fields=['state','updated_at'])
                return command_view(cmd)
            try:
                _snapshot_mapping(permit,lock=True)
                if cmd.intent.mapping_snapshot!=v1_machine.mapping_payload(permit.charger):
                    reject(409,'HAL_CHARGE_MAPPING_DRIFT','Frozen charging mapping has changed')
            except DomainError:
                # A frozen start cannot silently migrate to a different gun.
                # Do not leave a permanently eligible PERSISTED send in the queue.
                cmd.state='RECONCILIATION_REQUIRED'
                cmd.failure_code='HAL_MAPPING_CHANGED_BEFORE_SEND'
                cmd.save(update_fields=['state','failure_code','updated_at'])
                cmd.intent.state='RECONCILIATION_REQUIRED'
                cmd.intent.save(update_fields=['state','updated_at'])
                permit.state='RECONCILIATION_REQUIRED'
                permit.save(update_fields=['state','updated_at'])
                return command_view(cmd)
            if cmd.intent.credential_expires_at<=timezone.now():
                cmd.state='EXPIRED';cmd.failure_code='HAL_CREDENTIAL_EXPIRED_UNSENT'
                cmd.outcome_at=timezone.now()
                cmd.save(update_fields=['state','failure_code','outcome_at','updated_at'])
                cmd.intent.state='EXPIRED'
                cmd.intent.save(update_fields=['state','updated_at'])
                permit.state='RECONCILIATION_REQUIRED'
                permit.save(update_fields=['state','updated_at'])
                return command_view(cmd)
        else:
            session=HALV1ChargingSession.objects.select_for_update().filter(pk=cmd.session_id).first()
            if session is None or session.intent_id!=cmd.intent_id:
                reject(409,'HAL_CHARGE_STOP_IDENTITY_MISMATCH','Stop session identity changed')
            if session.stopped_at is not None:
                cmd.state='EXPIRED'
                cmd.failure_code='HAL_STOP_ALREADY_COMPLETED_UNSENT'
                cmd.outcome_at=timezone.now()
                cmd.save(update_fields=['state','failure_code','outcome_at','updated_at'])
                return command_view(cmd)
            if session.state not in {'ACTIVE','RECONCILIATION_REQUIRED'}:
                reject(409,'HAL_CHARGE_STOP_NOT_REQUIRED','No unresolved physical charging')
        frozen=dict(cmd.snapshot);correlation=str(cmd.correlation_id)
        if cmd.kind=='START':
            try:
                frozen['id_tag']=_cipher().decrypt(cmd.intent.credential_ciphertext.encode()).decode('ascii')
            except (InvalidToken,UnicodeError,ValueError):
                reject(409,'HAL_CHARGE_CREDENTIAL_UNREADABLE',
                       'Stored charging credential cannot be recovered; no send attempted')
        cmd.state='DELIVERY_ATTEMPTED';cmd.attempted_at=timezone.now()
        cmd.save(update_fields=['state','attempted_at','updated_at'])
        if cmd.kind=='START' and cmd.intent.state=='PERSISTED':
            cmd.intent.state='DELIVERY_ATTEMPTED'
            cmd.intent.save(update_fields=['state','updated_at'])
    result=None;failure=''
    try:
        provider=v1_machine.client()
        result=(provider.start(frozen,correlation) if cmd.kind=='START'
                else provider.stop(frozen,correlation))
    except Exception:
        # A transport failure can be ambiguous; do NOT automatically resend.
        failure='HAL_CHARGE_SEND_OUTCOME_UNKNOWN'
    # No request body or credential is ever included in diagnostic errors.
    with transaction.atomic():
        safety.lock_gate()
        locked=HALV1ChargingCommand.objects.select_for_update().get(pk=command_id)
        if locked.state=='DELIVERY_ATTEMPTED':
            if result and isinstance(result,dict) and isinstance(result.get('command'),dict):
                value=result['command']
                if (value.get('cms_command_id')!=str(locked.pk) or
                    value.get('kind')!=locked.kind or
                    value.get('state') not in GO_COMMAND_STATES):
                    result=None
                else:
                    try: locked.hal_command_id=UUID(canonical_uuid(value.get('hal_command_id')))
                    except ChargeContractError: result=None
                if result:
                    provider_state=value['state']
                    locked.provider_state=provider_state
                    if provider_state=='OCPP_ACCEPTED':
                        locked.state='PROTOCOL_ACKNOWLEDGED'
                        if locked.kind=='START' and locked.intent.state=='DELIVERY_ATTEMPTED':
                            locked.intent.state='PROTOCOL_ACKNOWLEDGED'
                            locked.intent.save(update_fields=['state','updated_at'])
                    elif provider_state in {'OCPP_REJECTED','EXPIRED','SUPERSEDED','AMBIGUOUS','MATERIALIZED'}:
                        locked.state='RECONCILIATION_REQUIRED'
                        if locked.kind=='START' and locked.intent.state=='DELIVERY_ATTEMPTED':
                            locked.intent.state='RECONCILIATION_REQUIRED'
                            locked.intent.save(update_fields=['state','updated_at'])
                    else:
                        locked.state='ACK_RECORDED'
                        if locked.kind=='START' and locked.intent.state=='DELIVERY_ATTEMPTED':
                            locked.intent.state='ACCEPTED_FOR_DELIVERY'
                            locked.intent.save(update_fields=['state','updated_at'])
                    locked.failure_code=('' if locked.state in {'ACK_RECORDED','PROTOCOL_ACKNOWLEDGED'}
                                         else 'HAL_CHARGE_PROVIDER_UNCERTAIN')
            if not result:
                locked.state='RECONCILIATION_REQUIRED'
                locked.failure_code=failure or 'HAL_CHARGE_RESPONSE_INVALID'
                if locked.kind=='START' and locked.intent.state=='DELIVERY_ATTEMPTED':
                    locked.intent.state='RECONCILIATION_REQUIRED'
                    locked.intent.save(update_fields=['state','updated_at'])
            locked.outcome_at=timezone.now()
            locked.save(update_fields=['state','hal_command_id','provider_state','failure_code','outcome_at','updated_at'])
        return command_view(locked)


def reconcile_command(command_id, *, actor=None):
    """Exact read only; GET 404/timeout is never permission for a new START."""
    with transaction.atomic():
        if actor is not None:
            safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        cmd=HALV1ChargingCommand.objects.select_for_update().filter(pk=command_id).first()
        if not cmd:reject(404,'HAL_CHARGE_COMMAND_NOT_FOUND','Command not found')
        if cmd.attempted_at is None:
            return command_view(cmd)
        expected=cmd.kind
    try:
        response=v1_machine.client().command(str(command_id))['command']
        if response.get('kind')!=expected:
            raise WireError('HAL_CHARGE_KIND_MISMATCH')
    except (WireError, KeyError, TypeError, ValueError):
        with transaction.atomic():
            if actor is not None:
                safety.fresh_admin(actor,'charging.reconcile')
            safety.lock_gate()
            cmd=HALV1ChargingCommand.objects.select_for_update().get(pk=command_id)
            if cmd.state not in {'MATERIALIZED','REJECTED','EXPIRED'}:
                cmd.state='RECONCILIATION_REQUIRED'
                cmd.failure_code='HAL_CHARGE_RECONCILIATION_UNCERTAIN'
                cmd.save(update_fields=['state','failure_code','updated_at'])
            return command_view(cmd)
    with transaction.atomic():
        if actor is not None:
            safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        cmd=HALV1ChargingCommand.objects.select_for_update().get(pk=command_id)
        if cmd.hal_command_id is not None and str(cmd.hal_command_id)!=response['hal_command_id']:
            reject(409,'HAL_CHARGE_COMMAND_ID_CHANGED','HAL command ID is inconsistent')
        cmd.hal_command_id=UUID(response['hal_command_id'])
        cmd.provider_state=response['state']
        if cmd.state not in {'MATERIALIZED','REJECTED','EXPIRED','RECONCILIATION_REQUIRED'}:
            if response['state']=='OCPP_ACCEPTED':
                cmd.state='PROTOCOL_ACKNOWLEDGED'
                if cmd.kind=='START' and cmd.intent.state not in {'ACTUALLY_STARTED','REJECTED','EXPIRED','RECONCILIATION_REQUIRED'}:
                    cmd.intent.state='PROTOCOL_ACKNOWLEDGED'
                    cmd.intent.save(update_fields=['state','updated_at'])
            elif response['state'] in {'OCPP_REJECTED','EXPIRED','SUPERSEDED'}:
                cmd.state='RECONCILIATION_REQUIRED'  # late start possible
            else:
                cmd.state='ACK_RECORDED' if response['state'] in {'PERSISTED','PENDING_DELIVERY','DELIVERY_ATTEMPTED'} else 'RECONCILIATION_REQUIRED'
            cmd.failure_code='' if cmd.state in {'ACK_RECORDED','PROTOCOL_ACKNOWLEDGED'} else 'HAL_CHARGE_PROVIDER_UNCERTAIN'
        cmd.save(update_fields=['hal_command_id','provider_state','state','failure_code','updated_at'])
        return command_view(cmd)


def reconcile_intent(intent_id, *, actor=None):
    """Exact Go transaction lookup, not a synthetic physical start or receipt.

    The lookup can prove Go knows a transaction, but only an authenticated,
    immutable Go fact can materialize CoW's session. Record the source evidence
    and explicitly request fact re-delivery through the operations workflow.
    """
    with transaction.atomic():
        if actor is not None:
            safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        intent=HALV1ChargingIntent.objects.select_for_update().select_related(
            'permit').filter(pk=intent_id).first()
        if intent is None:
            reject(404,'HAL_CHARGE_INTENT_NOT_FOUND','Charging intent not found')
        cmd=HALV1ChargingCommand.objects.filter(intent=intent,kind='START').first()
        if cmd is None or cmd.attempted_at is None:
            return {'intent_id':str(intent.pk),'provider_lookup':'NOT_ATTEMPTED',
                    'physical_session_derived':False}
        snap=dict(intent.mapping_snapshot)
        expected={'intent_id':str(intent.pk),
                  'charger_id':str(intent.permit.charger_id),
                  'connector_id':str(intent.permit.connector_id),
                  'scope_id':str(intent.permit.scope_id),
                  'identity':snap.get('charger_ocpp_identity'),
                  'connector_number':next((r.get('ocpp_connector_number')
                      for r in snap.get('connectors',[])
                      if isinstance(r,dict) and r.get('cms_connector_id')==str(intent.permit.connector_id)),None),
                  'cmd_id':str(cmd.pk)}
    try:
        result=v1_machine.client().transaction_for_intent(str(intent_id))
        response=result['transaction']
        if (response.get('cpo_id')!=expected['scope_id'] or
            response.get('cms_command_id')!=expected['cmd_id'] or
            response.get('cms_start_intent_id')!=expected['intent_id'] or
            response.get('cms_charger_id')!=expected['charger_id'] or
            response.get('cms_connector_id')!=expected['connector_id'] or
            response.get('charger_ocpp_identity')!=expected['identity'] or
            response.get('ocpp_connector_number')!=expected['connector_number']):
            raise WireError('HAL_CHARGE_RECONCILE_SCOPE_MISMATCH')
        hal_id=canonical_uuid(response.get('hal_transaction_id'))
        ocpp_id=strict_positive(response.get('ocpp_transaction_id'),'OCPP_TRANSACTION')
        source='PRESENT_UNPROJECTED'
    except (WireError,ChargeContractError,KeyError,TypeError):
        source='UNKNOWN_OR_CONFLICTING'
        hal_id=None;ocpp_id=None
    with transaction.atomic():
        if actor is not None:
            safety.fresh_admin(actor,'charging.reconcile')
        safety.lock_gate()
        intent=HALV1ChargingIntent.objects.select_for_update().filter(pk=intent_id).first()
        if intent is None:
            reject(404,'HAL_CHARGE_INTENT_NOT_FOUND','Charging intent not found')
        session=HALV1ChargingSession.objects.select_for_update().filter(intent=intent).first()
        if source=='PRESENT_UNPROJECTED' and session is not None:
            if str(session.hal_transaction_id)!=hal_id or session.ocpp_transaction_id!=ocpp_id:
                source='UNKNOWN_OR_CONFLICTING'
        if source!='PRESENT_UNPROJECTED':
            if intent.state!='ACTUALLY_STARTED':
                intent.state='RECONCILIATION_REQUIRED'
                intent.save(update_fields=['state','updated_at'])
            if session is not None and session.state in {'ACTIVE','PHYSICALLY_COMPLETED'}:
                session.state='RECONCILIATION_REQUIRED'
                session.save(update_fields=['state','updated_at'])
        _redact_audit(intent.pk,'HAL_V1_INTENT_PROVIDER_INSPECTED',principal=actor,
                      provider_lookup=source,session_present=bool(session))
        return {'intent_id':str(intent.pk),'provider_lookup':source,
                'physical_session_derived':False,
                'requires_immutable_fact_replay':source=='PRESENT_UNPROJECTED'}


def _is_same_chain(intent, payload, *, session=None):
    permit=intent.permit
    try:
        ensure_id_chain(payload,intent_id=str(intent.pk),charger_id=str(permit.charger_id),
            connector_id=str(permit.connector_id),identity=intent.mapping_snapshot['charger_ocpp_identity'],
            connector_number=next(row['ocpp_connector_number'] for row in intent.mapping_snapshot['connectors']
                                  if row['cms_connector_id']==str(permit.connector_id)),
            hal_transaction_id=(str(session.hal_transaction_id) if session else None),
            ocpp_transaction_id=(session.ocpp_transaction_id if session else None))
    except (ChargeContractError,KeyError,TypeError,StopIteration):
        return False
    return True


def _apply_command_updated(payload, mapping):
    try:
        command_id=UUID(canonical_uuid(payload.get('cms_command_id')))
        hal_id=UUID(canonical_uuid(payload.get('hal_command_id')))
    except ChargeContractError:
        return 'RECONCILIATION_REQUIRED'
    cmd=HALV1ChargingCommand.objects.select_for_update().select_related(
        'intent__permit__connector').filter(pk=command_id).first()
    if not cmd:
        return 'PENDING_BUSINESS'
    permit=cmd.intent.permit
    if (cmd.attempted_at is None or
        permit.charger_id!=mapping.charger_id or permit.scope_id!=mapping.scope_id or
        cmd.kind!=payload.get('kind') or
        cmd.snapshot.get('charger_ocpp_identity')!=payload.get('charger_ocpp_identity') or
        cmd.snapshot.get('ocpp_connector_number')!=payload.get('ocpp_connector_number') or
        (cmd.hal_command_id is not None and cmd.hal_command_id!=hal_id)):
        return 'RECONCILIATION_REQUIRED'
    provider_state=payload.get('state')
    from cow.hal.v1_charge_contract import GO_COMMAND_STATES
    if provider_state not in GO_COMMAND_STATES:
        return 'RECONCILIATION_REQUIRED'
    cmd.hal_command_id=hal_id;cmd.provider_state=provider_state
    if cmd.state not in {'MATERIALIZED','REJECTED','EXPIRED','RECONCILIATION_REQUIRED'}:
        if provider_state=='OCPP_ACCEPTED':
            cmd.state='PROTOCOL_ACKNOWLEDGED'
            if cmd.kind=='START' and cmd.intent.state not in {'ACTUALLY_STARTED','REJECTED','EXPIRED','RECONCILIATION_REQUIRED'}:
                cmd.intent.state='PROTOCOL_ACKNOWLEDGED'
                cmd.intent.save(update_fields=['state','updated_at'])
        elif provider_state in {'OCPP_REJECTED','EXPIRED','SUPERSEDED','AMBIGUOUS'}:
            cmd.state='RECONCILIATION_REQUIRED'
            if cmd.kind=='START' and cmd.intent.state!='ACTUALLY_STARTED':
                cmd.intent.state='RECONCILIATION_REQUIRED'
                cmd.intent.save(update_fields=['state','updated_at'])
        elif provider_state=='MATERIALIZED':
            # Fact is NOT substitute for transaction.started.
            cmd.state='RECONCILIATION_REQUIRED'
        elif provider_state in {'PERSISTED','PENDING_DELIVERY','DELIVERY_ATTEMPTED'}:
            if cmd.attempted_at is not None:cmd.state='ACK_RECORDED'
    cmd.save(update_fields=['hal_command_id','provider_state','state','updated_at'])
    return 'APPLIED'


def _apply_started(payload,mapping,occurred_at):
    try: intent_id=UUID(canonical_uuid(payload.get('cms_start_intent_id')))
    except ChargeContractError:return 'RECONCILIATION_REQUIRED'
    intent=HALV1ChargingIntent.objects.select_for_update().select_related(
        'permit__owner','permit__connector').filter(pk=intent_id).first()
    if not intent:return 'PENDING_BUSINESS'
    permit=intent.permit
    cmd=HALV1ChargingCommand.objects.select_for_update().filter(intent=intent,kind='START').first()
    if cmd is None or not _is_same_chain(intent,payload):
        return 'RECONCILIATION_REQUIRED'
    try:
        command_id=canonical_uuid(payload.get('cms_command_id'))
        hal_command_id=UUID(canonical_uuid(payload.get('hal_command_id')))
        hal_txn=UUID(canonical_uuid(payload.get('hal_transaction_id')))
        ocpp_txn=strict_positive(payload.get('ocpp_transaction_id'),'OCPP_TRANSACTION')
        meter_start=nonnegative(payload.get('meter_start_wh'),'METER_START')
        started_at=utc_time(payload.get('started_at'))
    except ChargeContractError:return 'RECONCILIATION_REQUIRED'
    tag=payload.get('id_tag')
    if (str(cmd.pk)!=command_id or cmd.attempted_at is None or
        cmd.hal_command_id not in (None,hal_command_id) or
        not isinstance(tag,str) or
        not hmac.compare_digest(hashlib.sha256(tag.encode()).hexdigest(),intent.credential_sha256) or
        mapping.charger_id!=permit.charger_id or mapping.scope_id!=permit.scope_id):
        return 'RECONCILIATION_REQUIRED'
    existing=HALV1ChargingSession.objects.select_for_update().filter(intent=intent).first()
    if existing:
        if (existing.hal_transaction_id!=hal_txn or existing.ocpp_transaction_id!=ocpp_txn or
            existing.meter_start_wh!=meter_start or existing.started_at!=started_at):
            return 'RECONCILIATION_REQUIRED'
        return 'APPLIED'
    if HALV1ChargingSession.objects.filter(connector=permit.connector,state__in=(
        'ACTIVE','RECONCILIATION_REQUIRED')).exists():
        return 'RECONCILIATION_REQUIRED'
    # Late contrary start is real physical evidence, not business authorization.
    late=(intent.state in {'REJECTED','EXPIRED','RECONCILIATION_REQUIRED'} or
          occurred_at>intent.command_expires_at or
          occurred_at>permit.expires_at or permit.state!='BOUND')
    session=HALV1ChargingSession.objects.create(
        intent=intent,charger_id=permit.charger_id,connector_id=permit.connector_id,
        hal_transaction_id=hal_txn,ocpp_transaction_id=ocpp_txn,
        state='RECONCILIATION_REQUIRED' if late else 'ACTIVE',
        started_at=started_at,meter_start_wh=meter_start,last_meter_wh=meter_start)
    cmd.hal_command_id=hal_command_id;cmd.state='MATERIALIZED'
    cmd.save(update_fields=['hal_command_id','state','updated_at'])
    intent.state='RECONCILIATION_REQUIRED' if late else 'ACTUALLY_STARTED'
    intent.credential_ciphertext=''
    intent.save(update_fields=['state','credential_ciphertext','updated_at'])
    if late:
        permit.state='RECONCILIATION_REQUIRED'
        permit.save(update_fields=['state','updated_at'])
    _redact_audit(session.pk,'HAL_V1_PHYSICAL_START_OBSERVED',
                  intent_id=str(intent.pk),hal_transaction_id=str(hal_txn),late=late)
    return 'RECONCILIATION_REQUIRED' if late else 'APPLIED'


def _lookup_session(payload):
    try: intent_id=UUID(canonical_uuid(payload.get('cms_start_intent_id')))
    except ChargeContractError:return None,None
    intent=HALV1ChargingIntent.objects.select_related('permit__connector').filter(pk=intent_id).first()
    if not intent:return None,None
    session=HALV1ChargingSession.objects.select_for_update().filter(intent=intent).first()
    return intent,session


def _apply_meter(payload):
    intent,session=_lookup_session(payload)
    if not intent or not session:return 'PENDING_BUSINESS'
    if not _is_same_chain(intent,payload,session=session):return 'RECONCILIATION_REQUIRED'
    try:
        seq=strict_positive(payload.get('meter_sequence'),'METER_SEQUENCE')
        energy=nonnegative(payload.get('meter_value_wh'),'METER')
        consumed=nonnegative(payload.get('consumed_wh'),'CONSUMED')
        observed=utc_time(payload.get('meter_observed_at'))
    except ChargeContractError:return 'RECONCILIATION_REQUIRED'
    if energy-session.meter_start_wh!=consumed:
        return 'RECONCILIATION_REQUIRED'
    if seq<=session.meter_sequence:return 'IGNORED_STALE'
    if session.stopped_at is not None and observed<=session.stopped_at and energy<=session.last_meter_wh:
        return 'IGNORED_STALE'
    if (session.state!='ACTIVE' or energy<session.last_meter_wh or
        session.meter_observed_at is not None and observed<session.meter_observed_at):
        return 'RECONCILIATION_REQUIRED'
    session.last_meter_wh=energy;session.meter_sequence=seq
    session.meter_observed_at=observed
    session.save(update_fields=['last_meter_wh','meter_sequence','meter_observed_at','updated_at'])
    return 'APPLIED'


def _apply_soc(payload):
    intent,session=_lookup_session(payload)
    if not intent or not session:return 'PENDING_BUSINESS'
    if not _is_same_chain(intent,payload,session=session):return 'RECONCILIATION_REQUIRED'
    try:
        seq=strict_positive(payload.get('soc_sequence'),'SOC_SEQUENCE')
        soc=valid_soc(payload.get('soc_percent'))
        observed=utc_time(payload.get('soc_observed_at'))
    except ChargeContractError:return 'RECONCILIATION_REQUIRED'
    if seq<=session.soc_sequence:return 'IGNORED_STALE'
    if session.stopped_at is not None and observed<=session.stopped_at:
        return 'IGNORED_STALE'
    if (session.state!='ACTIVE' or
        session.soc_observed_at is not None and observed<session.soc_observed_at):
        return 'RECONCILIATION_REQUIRED'
    session.latest_soc_percent=soc;session.soc_sequence=seq;session.soc_observed_at=observed
    session.save(update_fields=['latest_soc_percent','soc_sequence','soc_observed_at','updated_at'])
    return 'APPLIED'


def _apply_completed(payload,fact_id):
    intent,session=_lookup_session(payload)
    if not intent or not session:return 'PENDING_BUSINESS'
    if not _is_same_chain(intent,payload,session=session):return 'RECONCILIATION_REQUIRED'
    try:
        meter_start=nonnegative(payload.get('meter_start_wh'),'METER_START')
        meter_end=nonnegative(payload.get('meter_stop_wh'),'METER_STOP')
        stopped_at=utc_time(payload.get('stopped_at'))
    except ChargeContractError:return 'RECONCILIATION_REQUIRED'
    if (meter_start!=session.meter_start_wh or meter_end<session.last_meter_wh or
        stopped_at<session.started_at):
        return 'RECONCILIATION_REQUIRED'
    # HAL's one-Wh quantization evidence is OPTIONAL and must be exact if present.
    optional={'raw_meter_stop_wh','meter_stop_adjustment_wh','meter_stop_evidence'}
    present=optional.intersection(payload)
    if present:
        if present!=optional:return 'RECONCILIATION_REQUIRED'
        try:
            raw=nonnegative(payload['raw_meter_stop_wh'],'RAW_METER_STOP')
            adjusted=nonnegative(payload['meter_stop_adjustment_wh'],'METER_ADJUSTMENT')
        except ChargeContractError:return 'RECONCILIATION_REQUIRED'
        if (raw+adjusted!=meter_end or adjusted not in {0,1} or
            adjusted==1 and payload['meter_stop_evidence']!='QUANTIZATION_NORMALIZED'):
            return 'RECONCILIATION_REQUIRED'
    # Completion receipt identity is permanent, including for sessions held
    # in RECONCILIATION_REQUIRED. A second completion fact must never replace
    # its frozen end meter, stopped timestamp, or first authoritative fact ID.
    if session.completion_fact_id is not None:
        if session.completion_fact_id != UUID(fact_id):
            return 'RECONCILIATION_REQUIRED'
        return ('RECONCILIATION_REQUIRED' if session.state=='RECONCILIATION_REQUIRED'
                else 'APPLIED')
    if session.state in {'PHYSICALLY_COMPLETED','SETTLED'}:
        return 'RECONCILIATION_REQUIRED'
    if session.state not in {'ACTIVE','RECONCILIATION_REQUIRED'}:
        return 'RECONCILIATION_REQUIRED'
    stop=HALV1ChargingCommand.objects.select_for_update().filter(
        intent=intent,kind='STOP').first()
    # Go can stop autonomously (energy/time/charger event) without a CMS stop.
    # If Go names a CMS stop command, every identity must match durably.
    supplied_stop=payload.get('cms_command_id')
    supplied_hal_stop=payload.get('hal_command_id')
    stop_conflict=False
    if supplied_stop is not None or supplied_hal_stop is not None:
        try:
            stop_conflict=(stop is None or stop.attempted_at is None or
                supplied_stop is None or supplied_hal_stop is None or
                canonical_uuid(supplied_stop)!=str(stop.pk) or
                (stop.hal_command_id is not None and
                 canonical_uuid(supplied_hal_stop)!=str(stop.hal_command_id)))
            if not stop_conflict:
                canonical_uuid(supplied_hal_stop)
        except ChargeContractError:
            stop_conflict=True
    elif stop is not None:
        # Physical stop occurred, but it does not prove our staged command ran.
        stop_conflict=True
    was_held=(session.state=='RECONCILIATION_REQUIRED' or
              intent.state=='RECONCILIATION_REQUIRED' or
              intent.permit.state=='RECONCILIATION_REQUIRED' or stop_conflict)
    session.meter_end_wh=meter_end
    session.last_meter_wh=meter_end
    session.stopped_at=stopped_at
    session.stop_reason=str(payload.get('ocpp_stop_reason') or '')[:100]
    session.completion_fact_id=UUID(fact_id)
    session.state='RECONCILIATION_REQUIRED' if was_held else 'PHYSICALLY_COMPLETED'
    session.save(update_fields=['meter_end_wh','last_meter_wh','stopped_at',
                                'stop_reason','completion_fact_id','state','updated_at'])
    permit=HALV1ChargingPermit.objects.select_for_update().get(pk=intent.permit_id)
    permit.state='RECONCILIATION_REQUIRED' if was_held else 'SETTLEMENT_PENDING'
    permit.save(update_fields=['state','updated_at'])
    if stop and not stop_conflict:
        stop.hal_command_id=UUID(canonical_uuid(supplied_hal_stop))
        stop.state='MATERIALIZED'
        stop.save(update_fields=['hal_command_id','state','updated_at'])
    elif stop and stop_conflict:
        stop.state='RECONCILIATION_REQUIRED'
        stop.failure_code='HAL_STOP_PROVENANCE_UNPROVEN'
        stop.save(update_fields=['state','failure_code','updated_at'])
    _redact_audit(session.pk,'HAL_V1_PHYSICAL_COMPLETION_OBSERVED',
                  fact_id=fact_id,hal_transaction_id=str(session.hal_transaction_id),
                  stop_provenance_conflict=stop_conflict)
    # Physical completion is evidence, never permission to settle a held amount.
    return 'RECONCILIATION_REQUIRED' if was_held else 'APPLIED'


def apply_fact(fact_type,payload,mapping,occurred_at,fact_id):
    """Apply one authenticated Go fact, or durably quarantine an unsafe chain.

    No HTTP, payment, new charger command, or inferred business settlement.
    Caller holds the operational transaction gate and the receipt transaction.
    """
    if fact_type=='command.updated':
        state=_apply_command_updated(payload,mapping)
    elif fact_type=='transaction.started':
        state=_apply_started(payload,mapping,occurred_at)
    elif fact_type=='transaction.meter':
        state=_apply_meter(payload)
    elif fact_type=='transaction.soc':
        state=_apply_soc(payload)
    elif fact_type=='transaction.completed':
        state=_apply_completed(payload,fact_id)
    else:
        return 'PENDING_BUSINESS'
    if state=='RECONCILIATION_REQUIRED':
        # A known, malformed or contradictory authoritative fact must block
        # financial finalization and live use. Never reassign by proximity.
        intent=None
        if fact_type=='command.updated':
            raw=payload.get('cms_command_id')
            try:
                command_id=UUID(canonical_uuid(raw))
            except ChargeContractError:
                command_id=None
            if command_id is not None:
                command=HALV1ChargingCommand.objects.filter(pk=command_id).first()
                if command is not None:
                    intent=HALV1ChargingIntent.objects.select_for_update().filter(
                        pk=command.intent_id,permit__scope_id=mapping.scope_id,
                        permit__charger_id=mapping.charger_id).first()
        else:
            try:
                intent_id=UUID(canonical_uuid(payload.get('cms_start_intent_id')))
            except ChargeContractError:
                intent_id=None
            if intent_id is not None:
                intent=HALV1ChargingIntent.objects.select_for_update().filter(
                    pk=intent_id,permit__scope_id=mapping.scope_id,
                    permit__charger_id=mapping.charger_id).first()
        if intent is not None:
            session=HALV1ChargingSession.objects.select_for_update().filter(intent=intent).first()
            if session is not None and session.state in {'ACTIVE','PHYSICALLY_COMPLETED'}:
                session.state='RECONCILIATION_REQUIRED'
                session.save(update_fields=['state','updated_at'])
            if intent.state != 'RECONCILIATION_REQUIRED':
                intent.state='RECONCILIATION_REQUIRED'
                intent.save(update_fields=['state','updated_at'])
            permit=HALV1ChargingPermit.objects.select_for_update().get(pk=intent.permit_id)
            if permit.state not in {'SETTLED','RELEASED','RECONCILIATION_REQUIRED'}:
                permit.state='RECONCILIATION_REQUIRED'
                permit.save(update_fields=['state','updated_at'])
            _redact_audit(intent.pk,'HAL_V1_FACT_REQUIRES_RECONCILIATION',
                          fact_id=fact_id,fact_type=fact_type)
    if fact_type in {'transaction.started','transaction.completed',
                     'transaction.meter','transaction.soc','command.updated'}:
        from cow.services.booking_commercial import reflect_hal_fact_locked
        reflect_hal_fact_locked(fact_type,payload,state)
    return state


def replay_pending(*,limit=50):
    """Bounded local receipt replay, never HAL network replay or command send."""
    if type(limit) is not int or not 1<=limit<=100:
        reject(422,'HAL_REPLAY_LIMIT_INVALID','Replay limit invalid')
    changed=0
    ids=list(HALV1FactReceipt.objects.filter(processing_state='PENDING_BUSINESS',
        fact_type__in=['transaction.started','transaction.meter','transaction.soc',
                       'transaction.completed','command.updated']
        ).order_by('received_at','fact_id').values_list('fact_id',flat=True)[:limit])
    for fact_id in ids:
        with transaction.atomic():
            safety.lock_gate()
            receipt=HALV1FactReceipt.objects.select_for_update().filter(
                fact_id=fact_id,processing_state='PENDING_BUSINESS').first()
            if receipt is None:continue
            mapping=HALV1Mapping.objects.filter(charger=receipt.charger,
                scope_id=receipt.scope_id).first()
            if not mapping:continue
            payload=restore_sensitive_payload(receipt.fact_type,receipt.payload)
            from cow.hal.v1_ingress import _mapping
            verified=_mapping(payload,receipt.fact_type)
            if verified.pk!=mapping.pk:
                reject(409,'HAL_RECEIPT_SCOPE_DRIFT','Receipt mapping no longer belongs to its charger')
            state=apply_fact(receipt.fact_type,payload,verified,receipt.occurred_at,str(fact_id))
            if state!='PENDING_BUSINESS':
                receipt.processing_state=state
                receipt.save(update_fields=['processing_state'])
                changed+=1
    return changed


def seal_sensitive_payload(fact_type,payload):
    if fact_type!='transaction.started' or not isinstance(payload.get('id_tag'),str):
        return payload
    clean=dict(payload)
    clean['id_tag_ciphertext']=_cipher().encrypt(clean.pop('id_tag').encode()).decode()
    return clean


def restore_sensitive_payload(fact_type,payload):
    if fact_type!='transaction.started' or 'id_tag_ciphertext' not in payload:
        return payload
    clean=dict(payload)
    try:clean['id_tag']=_cipher().decrypt(clean.pop('id_tag_ciphertext').encode()).decode()
    except (InvalidToken,UnicodeError):
        reject(409,'HAL_FACT_CREDENTIAL_UNREADABLE','Encrypted evidence cannot be decoded')
    return clean


def owner_intent(owner,intent_id):
    intent=HALV1ChargingIntent.objects.select_related('permit').filter(
        pk=intent_id,permit__owner=owner).first()
    if not intent:reject(404,'HAL_CHARGE_INTENT_NOT_FOUND','Charging intent not found')
    return intent_view(intent)


def owner_session(owner,session_id):
    session=HALV1ChargingSession.objects.select_related('intent__permit').filter(
        pk=session_id,intent__permit__owner=owner).first()
    if not session:reject(404,'HAL_CHARGE_SESSION_NOT_FOUND','Charging session not found')
    return session_view(session)


def session_view(session):
    permit=session.intent.permit
    connection=HALV1ChargerRuntime.objects.filter(charger_id=permit.charger_id).first()
    status=HALV1ConnectorRuntime.objects.filter(connector_id=permit.connector_id).first()
    now=timezone.now()
    connection_window=settings.COW_HAL_V1_CONNECTION_STALE_SECONDS
    current=HALV1Mapping.objects.filter(charger_id=permit.charger_id,scope_id=permit.scope_id).first()
    mapping_still_valid=bool(current and current.state=='SYNCHRONIZED' and
                             current.snapshot==session.intent.mapping_snapshot)
    live=bool(mapping_still_valid and connection and connection.connection_state=='ONLINE' and
        connection.observed_at and 0<=(now-connection.observed_at).total_seconds()<=connection_window)
    def freshness(observed,window):
        if observed is None:return 'UNKNOWN'
        delta=(now-observed).total_seconds()
        return 'FRESH' if live and 0<=delta<=window else 'STALE'
    return {
        'id':str(session.pk),'intent_id':str(session.intent_id),
        'state':session.state,'channel':permit.channel,
        'started_at':_strtime(session.started_at),
        'completed_at':_strtime(session.stopped_at) if session.stopped_at else None,
        'connection':{'state':connection.connection_state if connection else 'UNKNOWN',
            'observed_at':_strtime(connection.observed_at) if connection and connection.observed_at else None,
            'freshness':'FRESH' if live else ('STALE' if connection and connection.observed_at else 'UNKNOWN')},
        'connector_live':{'last_ocpp_status':status.ocpp_status if status else None,
            'observed_at':_strtime(status.observed_at) if status and status.observed_at else None,
            'freshness':freshness(status.observed_at if status else None,
                                  settings.COW_HAL_V1_CONNECTOR_STALE_SECONDS)},
        'meter':{'latest_wh':session.last_meter_wh,
            'consumed_wh':session.last_meter_wh-session.meter_start_wh,
            'sequence':session.meter_sequence,
            'observed_at':_strtime(session.meter_observed_at) if session.meter_observed_at else None,
            'freshness':freshness(session.meter_observed_at,
                                  settings.COW_HAL_V1_METER_STALE_SECONDS)},
        'soc':{'percent':(str(session.latest_soc_percent)
                           if session.latest_soc_percent is not None else None),
            'sequence':session.soc_sequence,
            'observed_at':_strtime(session.soc_observed_at) if session.soc_observed_at else None,
            'freshness':freshness(session.soc_observed_at,
                                  settings.COW_HAL_V1_SOC_STALE_SECONDS)},
        'settlement_state':'PENDING' if session.state=='PHYSICALLY_COMPLETED' else
                           'SETTLED' if session.state=='SETTLED' else 'NOT_READY',
    }
