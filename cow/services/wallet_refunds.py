"""Two-person approved WALLET-CREDIT refunds; NOT payment-gateway reversals.

The original settled booking is the ceiling of all non-rejected refunds. Tax
credit notes/PSP refunds require their own future legal/provider operations.
"""
from __future__ import annotations
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from cow.common.errors import reject
from cow.models import CoWBookingFinancialSettlement, CoWWalletRefund
from cow.services import operational_eligibility as safety
from cow.services import wallet_authority as wallet_ledger


def _enabled():
    if not getattr(settings,'COW_WALLET_REFUND_POST_ENABLED',False):
        reject(503,'WALLET_REFUNDS_DISABLED','Two-person wallet refund release gate is disabled')


def _text(value,min_chars=12,max_chars=160):
    if not isinstance(value,str) or not min_chars<=len(value)<=max_chars or any(ord(c)<32 for c in value):
        reject(422,'FINANCE_EVIDENCE_INVALID','Bounded audit reference required')
    return value


def _view(row):
    return {'id':str(row.pk),'settlement_id':str(row.settlement_id),
            'amount_paise':row.amount_paise,'state':row.state,
            'requested_by':str(row.requested_by_id),
            'approved_by':str(row.approved_by_id) if row.approved_by_id else None,
            'wallet_transaction_id':str(row.wallet_transaction_id) if row.wallet_transaction_id else None,
            'refund_type':'WALLET_CREDIT_NOT_PSP_REFUND'}


def request_wallet_refund(*,actor,settlement_id,amount_paise,request_key,reason,evidence_ref):
    _enabled();_text(request_key,16,128);_text(evidence_ref)
    if type(amount_paise) is not int or amount_paise<=0 or amount_paise >= 2**63:
        reject(422,'REFUND_AMOUNT_INVALID','Positive integer refund paise required')
    if not isinstance(reason,str) or not 12<=len(reason)<=500:
        reject(422,'REFUND_REASON_INVALID','12-500 character reason required')
    with transaction.atomic():
        safety.fresh_admin(actor,'refunds.manage')
        safety.lock_gate()
        s=CoWBookingFinancialSettlement.objects.select_for_update().select_related('booking').filter(
            pk=settlement_id,state='POSTED').first()
        if s is None or s.wallet_transaction_id is None:
            reject(409,'REFUND_SETTLEMENT_NOT_POSTED','Posted settlement required')
        old=CoWWalletRefund.objects.select_for_update().filter(settlement=s,request_key=request_key).first()
        if old is not None:
            if (old.amount_paise!=amount_paise or old.reason!=reason or old.evidence_ref!=evidence_ref):
                reject(409,'REFUND_IDEMPOTENCY_CONFLICT','Refund key changed scope or amount')
            return _view(old)
        reserved=CoWWalletRefund.objects.filter(settlement=s,state__in=['REQUESTED','POSTED']).aggregate(
            n=Sum('amount_paise'))['n'] or 0
        if reserved+amount_paise>s.final_gross_paise:
            reject(409,'REFUND_CEILING_EXCEEDED','Refunds cannot exceed originally posted booking debit')
        row=CoWWalletRefund.objects.create(settlement=s,amount_paise=amount_paise,
            request_key=request_key,reason=reason,evidence_ref=evidence_ref,
            requested_by=actor,state='REQUESTED')
        return _view(row)


def approve_and_credit(*,actor,refund_id,approval_ref):
    _enabled();_text(approval_ref)
    with transaction.atomic():
        safety.fresh_admin(actor,'refunds.manage')
        safety.lock_gate()
        # All refund writers lock settlement BEFORE refund row to avoid deadlocks.
        scope=CoWWalletRefund.objects.filter(pk=refund_id).values('settlement_id').first()
        if scope is None:reject(404,'REFUND_NOT_FOUND','Refund request not found')
        s=CoWBookingFinancialSettlement.objects.select_for_update().select_related('booking').get(
            pk=scope['settlement_id'])
        r=CoWWalletRefund.objects.select_for_update().filter(pk=refund_id,settlement=s).first()
        if r is None:reject(409,'REFUND_SCOPE_CHANGED','Refund scope changed during review')
        if r.state=='POSTED':
            if r.approval_ref!=approval_ref:
                reject(409,'REFUND_APPROVAL_REPLAY_CONFLICT','Posted refund approval differs')
            return _view(r)
        if r.state!='REQUESTED':
            reject(409,'REFUND_NOT_REQUESTED','Refund is no longer pending')
        if r.requested_by_id==actor.pk:
            reject(403,'REFUND_SELF_APPROVAL_FORBIDDEN','A second authorized Admin must approve')
        if s.state!='POSTED' or s.wallet_transaction_id is None:
            reject(409,'REFUND_SETTLEMENT_CHANGED','Original financial settlement unproven')
        total=CoWWalletRefund.objects.filter(settlement=s,state='POSTED').aggregate(n=Sum('amount_paise'))['n'] or 0
        if total+r.amount_paise>s.final_gross_paise:
            reject(409,'REFUND_TOTAL_EXCEEDS_DEBIT','Refund would exceed original debit')
        # No wallet debit or reservation release: this is an authorized credit.
        tx=wallet_ledger.refund_wallet_credit(
            s.booking.wallet,r.amount_paise,r.pk,s.booking_id,approval_ref)
        r.approved_by=actor;r.approval_ref=approval_ref
        r.wallet_transaction=tx;r.state='POSTED'
        r.save(update_fields=['approved_by','approval_ref','wallet_transaction','state','updated_at'])
        return _view(r)
