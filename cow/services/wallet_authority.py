"""Single CoW wallet write authority, required by all new money-moving flows.

Never infer a PSP capture from user input, and never debit a reservation owned by
another booking. All functions require transaction.atomic() and serialize on the
WalletAccount row. Booking release is permitted ONLY for its own posted settlement
in the same SQL transaction. No methods grant a role or assert tax/legal facts.
"""
from __future__ import annotations

from django.db import connection
from django.db.models import Sum

from cow.common.errors import reject
from cow.models import (
    WalletAccount, WalletTransaction, CoWBookingCommercialCommitment,
    CoWWalletOperation,
)
from cow.services.charging_commercial_policy import stable_digest

ACTIVE = ('FUNDED','START_PENDING','IN_SERVICE','CLOSING','RECONCILIATION_REQUIRED')
LIMIT = (1 << 63) - 1


def _atomic():
    if not connection.in_atomic_block:
        raise RuntimeError('Wallet operation requires an existing atomic transaction')


def _amount(n):
    if type(n) is not int or not (0 < n <= LIMIT):
        reject(422,'WALLET_AMOUNT_INVALID','Positive integer paise within bound required')


def _key(v):
    if type(v) is not str or not 16 <= len(v) <= 128 or any(ord(c)<32 or ord(c)==127 for c in v):
        reject(422,'WALLET_KEY_INVALID','Stable 16-128 character wallet operation key required')


def locked_wallet(wallet_or_id):
    _atomic()
    pk = getattr(wallet_or_id,'pk',wallet_or_id)
    wallet = WalletAccount.objects.select_for_update().filter(pk=pk).first()
    if wallet is None or wallet.currency!='INR' or type(wallet.balance_paise) is not int or wallet.balance_paise<0:
        reject(409,'WALLET_AUTHORITY_INVALID','Wallet absent, invalid or not INR')
    return wallet


def holds_locked(wallet):
    """Read only AFTER acquiring the wallet row lock; all writers must do so."""
    _atomic()
    total = CoWBookingCommercialCommitment.objects.filter(
        wallet=wallet,state__in=ACTIVE).aggregate(n=Sum('reserved_paise'))['n'] or 0
    if type(total) is not int or total<0 or total>wallet.balance_paise:
        reject(409,'WALLET_HOLDS_OVERCOMMITTED','Wallet reservations require reconciliation')
    return total


def available_locked(wallet):
    return wallet.balance_paise - holds_locked(wallet)


def _existing(wallet,op_key,kind,amount,request_digest):
    old = CoWWalletOperation.objects.select_for_update().select_related('wallet_transaction').filter(
        wallet=wallet,operation_key=op_key).first()
    if old is None:
        return None
    if (old.kind!=kind or old.amount_paise!=amount or
        old.request_sha256!=request_digest or old.wallet_transaction_id is None or
        old.state!='POSTED'):
        reject(409,'WALLET_IDEMPOTENCY_CONFLICT','Wallet operation key reused with different effects')
    return old.wallet_transaction


def _post(wallet,amount,kind,operation_key,details,*,credit,release_booking=None,reference_id=None):
    _atomic(); _amount(amount); _key(operation_key)
    if not isinstance(details,dict) or 'currency' not in details or details['currency']!='INR':
        reject(422,'WALLET_EVIDENCE_INVALID','Canonical INR evidence object required')
    request_digest=stable_digest({'amount_paise':amount,'kind':kind,'details':details,
                                  'release_booking_id':str(release_booking) if release_booking else None})
    wallet=locked_wallet(wallet)
    prior=_existing(wallet,operation_key,kind,amount,request_digest)
    if prior is not None:
        return prior
    held=holds_locked(wallet)
    if credit:
        if amount > LIMIT-wallet.balance_paise:
            reject(409,'WALLET_BALANCE_OVERFLOW','Wallet cannot safely accept credit')
        resulting=wallet.balance_paise+amount
    else:
        owned=0
        if release_booking is not None:
            booking=CoWBookingCommercialCommitment.objects.select_for_update().filter(
                pk=release_booking,wallet=wallet,state__in=ACTIVE).first()
            if booking is None:
                reject(409,'WALLET_RESERVATION_OWNER_INVALID','Reserved debit not owned by booking')
            owned=booking.reserved_paise
            if owned < amount+booking.protected_buffer_paise:
                reject(409,'WALLET_BOOKING_HOLD_INSUFFICIENT','Booking has not reserved debit plus buffer')
        if amount > wallet.balance_paise-(held-owned):
            reject(409,'WALLET_AVAILABLE_INSUFFICIENT','Debit would consume another booking reservation')
        if amount>wallet.balance_paise:
            reject(409,'WALLET_NEGATIVE_FORBIDDEN','Wallet cannot become negative')
        resulting=wallet.balance_paise-amount
    wallet.balance_paise=resulting
    wallet.version+=1
    wallet.save(update_fields=['balance_paise','version','updated_at'])
    tx=WalletTransaction.objects.create(
        wallet=wallet,transaction_type=kind,amount_paise=amount,
        balance_after_paise=resulting,reference_id=reference_id or operation_key[:128],
        idempotency_key=operation_key,details=details)
    CoWWalletOperation.objects.create(
        wallet=wallet,operation_key=operation_key,kind=kind,
        amount_paise=amount,request_sha256=request_digest,
        wallet_transaction=tx,state='POSTED')
    return tx


def credit_captured_topup(wallet,amount,provider_order_id,payment_id,safe_evidence):
    """Caller MUST have independently verified provider capture while locking PaymentOrder.

    Preserve the existing allowlisted Razorpay evidence in WalletTransaction.details,
    so existing statements and audit consumers retain exactly the same evidence.
    """
    if (not isinstance(safe_evidence,dict) or
        safe_evidence.get('status')!='captured' or
        safe_evidence.get('id')!=payment_id or
        safe_evidence.get('order_id')!=provider_order_id or
        safe_evidence.get('amount')!=amount or
        safe_evidence.get('currency')!='INR'):
        reject(409,'WALLET_PROVIDER_CAPTURE_EVIDENCE_INVALID',
               'Sanitized, verified payment capture evidence must match order')
    return _post(wallet,amount,'CREDIT_TOPUP','cow:topup:'+str(payment_id),
                 safe_evidence,credit=True,reference_id=str(payment_id))


def debit_booking_settlement(wallet,booking,amount,final_sha256,job_id):
    return _post(wallet,amount,'COW_BOOKING_CHARGE',
        'cow:booking:settle:'+str(booking.pk),
        {'currency':'INR','booking_id':str(booking.pk),'job_id':str(job_id),
         'final_sha256':str(final_sha256),'not_a_tax_invoice':True},
        credit=False,release_booking=booking.pk)


def refund_wallet_credit(wallet,amount,refund_id,booking_id,approval_ref):
    """Wallet credit only. NOT a refund to the originating PSP or payment method."""
    return _post(wallet,amount,'COW_WALLET_REFUND',
        'cow:refund:'+str(refund_id),
        {'currency':'INR','refund_id':str(refund_id),'booking_id':str(booking_id),
         'approval_ref':str(approval_ref),'wallet_credit_only':True},credit=True)
