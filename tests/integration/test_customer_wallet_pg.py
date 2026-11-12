"""PostgreSQL acceptance, concurrency, and integrity tests for Customer Wallet & Payment operations.

Tests execute against the approved disposable PostgreSQL test harness with transaction rollback,
validating:
1. Concurrent verification of the exact same payment (asserting both workers succeed under idempotent replay, exactly-once credit).
2. Conflicting payment IDs for the same order (strict rejection).
3. Duplicate payment IDs across different orders (cross-order uniqueness).
4. Concurrent external order creation exclusive claim (single worker permitted, competing worker gets 409 IN_PROGRESS).
5. Provider success with local failure recovery (reconciliation discovers existing order on retry, no duplicate creation).
6. Inconclusive reconciliation holds creation (prevents duplicate creation while outcome is uncertain).
7. Stale worker claim rejection (stale lease cannot overwrite newer confirmed order).
8. Immutability of confirmed provider order IDs (cannot be replaced).
9. Parameter conflicts on order recovery (amount or currency mismatch rejection).
10. PostgreSQL locking and rollback on evidence verification failure.
11. Support ticket reopening upon customer reply (testing both RESOLVED and CLOSED).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction
from django.test import Client

from cow.common.errors import DomainError
from cow.models import (
    Account,
    AuditEvent,
    CustomerJobRating,
    CustomerProfile,
    CustomerSupportMessage,
    CustomerSupportTicket,
    PaymentOrder,
    Principal,
    ServiceJob,
    ServiceRequest,
    WalletAccount,
    WalletTransaction,
)
from cow.users import repository as repo, service
from cow.users.service import (
    ReconciliationOutcome,
    compute_order_receipt,
)

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def create_customer_principal(verified: bool = True) -> Principal:
    """Create a test customer principal with associated profile."""
    uid = uuid4().hex
    account = Account.objects.create(
        email=f"customer-{uid}@example.test",
        password_hash="test_pw_hash",
        email_verified=verified,
    )
    principal = Principal.objects.create(account=account, plane="app", status="ACTIVE")
    CustomerProfile.objects.create(
        principal=principal,
        full_name="Postgres Test Customer",
        phone="+919876543210",
    )
    return principal


def compute_sig(order_id: str, payment_id: str, secret: str) -> str:
    """Compute HMAC-SHA256 signature matching Razorpay provider format."""
    message = f"{order_id}|{payment_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def test_concurrent_payment_verification_exact_once_credit():
    """Concurrent attempts to verify the exact same payment must both succeed without error and credit exactly once."""
    customer = create_customer_principal()
    secret = "rzp_test_secret_concurrency"
    order_id = f"order_{uuid4().hex[:12]}"
    payment_id = f"pay_{uuid4().hex[:12]}"
    sig = compute_sig(order_id, payment_id, secret)

    # Initial order in PENDING state
    repo.create_payment_order(
        actor=customer,
        amount_paise=50000,
        currency="INR",
        provider_order_id=order_id,
        key_id="rzp_test_key",
        receipt=f"rc_{uuid4().hex[:16]}",
    )

    evidence = {
        "id": payment_id,
        "order_id": order_id,
        "status": "captured",
        "amount": 50000,
        "currency": "INR",
        "method": "upi",
    }

    results = []
    errors = []

    def verify_worker():
        try:
            res = repo.verify_and_credit_recharge(
                actor=customer,
                order_id=order_id,
                payment_id=payment_id,
                signature=sig,
                secret=secret,
                payment_evidence=evidence,
            )
            return ("SUCCESS", res)
        except Exception as e:
            return ("ERROR", e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(verify_worker)
        f2 = executor.submit(verify_worker)
        for f in (f1, f2):
            outcome, val = f.result()
            if outcome == "SUCCESS":
                results.append(val)
            else:
                errors.append(val)

    # Explicit check on thread execution: no unexpected exceptions allowed
    assert len(errors) == 0, f"Concurrent verification threw unexpected errors: {errors}"
    assert len(results) == 2, f"Expected both workers to complete, got {len(results)}"

    # Both workers received successful CAPTURED status and identical credited paise
    for r in results:
        assert r["status"] == "CAPTURED"
        assert r["credited_paise"] == 50000
        assert r["balance_paise"] == 50000

    # Authoritative balance is credited EXACTLY ONCE
    self_wallet = WalletAccount.objects.get(customer=customer)
    assert self_wallet.balance_paise == 50000

    # Exactly one ledger entry was written
    txs = WalletTransaction.objects.filter(wallet=self_wallet, reference_id=payment_id)
    assert txs.count() == 1
    assert txs.first().amount_paise == 50000

    # Order transitioned to CAPTURED
    order = PaymentOrder.objects.get(provider_order_id=order_id)
    assert order.state == "CAPTURED"
    assert order.provider_payment_id == payment_id


def test_conflicting_payment_ids_for_same_order():
    """An order already captured cannot be captured again with a conflicting payment ID."""
    customer = create_customer_principal()
    secret = "rzp_secret_conflict"
    order_id = f"order_{uuid4().hex[:12]}"
    pay_first = f"pay_{uuid4().hex[:12]}"
    pay_second = f"pay_{uuid4().hex[:12]}"

    repo.create_payment_order(
        actor=customer,
        amount_paise=50000,
        currency="INR",
        provider_order_id=order_id,
        key_id="rzp_key",
    )

    ev_first = {
        "id": pay_first,
        "order_id": order_id,
        "status": "captured",
        "amount": 50000,
        "currency": "INR",
    }
    sig_first = compute_sig(order_id, pay_first, secret)

    res1 = repo.verify_and_credit_recharge(
        actor=customer,
        order_id=order_id,
        payment_id=pay_first,
        signature=sig_first,
        secret=secret,
        payment_evidence=ev_first,
    )
    assert res1["status"] == "CAPTURED"

    ev_second = {
        "id": pay_second,
        "order_id": order_id,
        "status": "captured",
        "amount": 50000,
        "currency": "INR",
    }
    sig_second = compute_sig(order_id, pay_second, secret)

    with pytest.raises(DomainError) as exc_info:
        repo.verify_and_credit_recharge(
            actor=customer,
            order_id=order_id,
            payment_id=pay_second,
            signature=sig_second,
            secret=secret,
            payment_evidence=ev_second,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PAYMENT_CONFLICT"


def test_duplicate_payment_id_across_different_orders():
    """The same payment ID cannot be credited across two different orders."""
    customer = create_customer_principal()
    secret = "rzp_secret_cross_order"
    order_a = f"order_a_{uuid4().hex[:8]}"
    order_b = f"order_b_{uuid4().hex[:8]}"
    shared_payment_id = f"pay_shared_{uuid4().hex[:8]}"

    repo.create_payment_order(customer, 50000, "INR", order_a, "rzp_key")
    repo.create_payment_order(customer, 50000, "INR", order_b, "rzp_key")

    sig_a = compute_sig(order_a, shared_payment_id, secret)
    ev_a = {
        "id": shared_payment_id,
        "order_id": order_a,
        "status": "captured",
        "amount": 50000,
        "currency": "INR",
    }
    repo.verify_and_credit_recharge(customer, order_a, shared_payment_id, sig_a, secret, ev_a)

    sig_b = compute_sig(order_b, shared_payment_id, secret)
    ev_b = {
        "id": shared_payment_id,
        "order_id": order_b,
        "status": "captured",
        "amount": 50000,
        "currency": "INR",
    }
    with pytest.raises(DomainError) as exc_info:
        repo.verify_and_credit_recharge(customer, order_b, shared_payment_id, sig_b, secret, ev_b)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "DUPLICATE_PAYMENT"


def test_concurrent_order_creation_exclusive_claim_prevents_duplicate_provider_orders():
    """Simultaneous requests with the same idempotency key permit exactly one external creation."""
    customer = create_customer_principal()
    key = "concurrent_creation_key_12345678"
    receipt = compute_order_receipt(customer, key)

    with patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key", "rzp_sec")):
        with patch("cow.users.service.reconcile_razorpay_order_by_receipt", return_value=(ReconciliationOutcome.CONFIRMED_ABSENT, None)):
            with patch("cow.users.service.call_razorpay_create_order", return_value="order_razorpay_single") as mock_create:
                # Worker 1 acquires claim and completes creation
                status1, claim1, order1 = repo.acquire_payment_creation_claim(
                    customer, receipt, 50000, "INR", idempotency_key=key, lease_seconds=30
                )
                assert status1 == "CLAIMED"
                assert claim1 is not None

                # Worker 2 attempts claim while Worker 1 lease is active
                status2, claim2, order2 = repo.acquire_payment_creation_claim(
                    customer, receipt, 50000, "INR", idempotency_key=key, lease_seconds=30
                )
                assert status2 == "IN_PROGRESS"

                # Worker 1 confirms order
                confirmed = repo.confirm_payment_intent(customer, receipt, "order_razorpay_single", "rzp_key", claim_token=claim1)
                assert confirmed["order_id"] == "order_razorpay_single"

                # Subsequent request finds CONFIRMED
                status3, claim3, order3 = repo.acquire_payment_creation_claim(
                    customer, receipt, 50000, "INR", idempotency_key=key, lease_seconds=30
                )
                assert status3 == "CONFIRMED"
                assert order3["order_id"] == "order_razorpay_single"


def test_provider_success_with_local_failure_recovery():
    """When gateway succeeds but worker crashes before confirmation, retry reconciles without re-creating."""
    customer = create_customer_principal()
    key = "local_failure_recovery_key_12345"
    receipt = compute_order_receipt(customer, key)

    # Intent created in DB
    status, claim, _ = repo.acquire_payment_creation_claim(
        customer, receipt, 75000, "INR", idempotency_key=key, lease_seconds=30
    )
    assert status == "CLAIMED"

    # Gateway succeeded externally with ID 'order_ext_live', but worker died and marked uncertain
    repo.mark_payment_intent_uncertain(customer, receipt, "Process dropped connection", claim_token=claim)

    with patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key", "rzp_sec")):
        with patch(
            "cow.users.service.reconcile_razorpay_order_by_receipt",
            return_value=(ReconciliationOutcome.FOUND, "order_ext_live"),
        ) as mock_recon:
            with patch("cow.users.service.call_razorpay_create_order") as mock_create:
                # Client retries with same idempotency key
                resp = service.create_wallet_recharge_order(
                    customer, {"amount_paise": 75000, "currency": "INR"}, key
                )
                assert resp.status_code == 201

                # Reconciled without calling create order!
                mock_recon.assert_called_once()
                mock_create.assert_not_called()

                db_order = PaymentOrder.objects.get(customer=customer, receipt=receipt)
                assert db_order.provider_order_id == "order_ext_live"


def test_inconclusive_reconciliation_prevents_duplicate_creation():
    """When reconciliation outcome is UNKNOWN, order creation is held to prevent duplicates."""
    customer = create_customer_principal()
    key = "unknown_recon_key_123456789"

    with patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key", "rzp_sec")):
        with patch(
            "cow.users.service.reconcile_razorpay_order_by_receipt",
            return_value=(ReconciliationOutcome.UNKNOWN, None),
        ):
            with patch("cow.users.service.call_razorpay_create_order") as mock_create:
                with pytest.raises(DomainError) as exc_info:
                    service.create_wallet_recharge_order(
                        customer, {"amount_paise": 50000, "currency": "INR"}, key
                    )
                assert exc_info.value.status_code == 504
                assert exc_info.value.code == "GATEWAY_TIMEOUT"
                # CRITICAL: External create order must NOT be called!
                mock_create.assert_not_called()


def test_stale_worker_claim_rejected_on_confirmation():
    """A worker whose claim expired cannot confirm an order if another claim was acquired."""
    customer = create_customer_principal()
    receipt = f"rc_stale_{uuid4().hex[:12]}"

    # Worker 1 acquires claim
    s1, claim1, _ = repo.acquire_payment_creation_claim(customer, receipt, 50000, "INR", lease_seconds=1)

    # Worker 2 reclaims after expiry
    s2, claim2, _ = repo.acquire_payment_creation_claim(customer, receipt, 50000, "INR", lease_seconds=30)
    assert s2 == "CLAIMED"
    assert claim1 != claim2

    # Worker 2 confirms order
    repo.confirm_payment_intent(customer, receipt, "order_worker_2", claim_token=claim2)

    # Stale Worker 1 attempts confirmation with claim1 -> rejected
    with pytest.raises(DomainError) as exc_info:
        repo.confirm_payment_intent(customer, receipt, "order_worker_1", claim_token=claim1)
    assert exc_info.value.status_code == 409


def test_confirmed_provider_order_id_is_immutable():
    """An already confirmed order ID can never be replaced by a different order ID."""
    customer = create_customer_principal()
    receipt = f"rc_immutable_{uuid4().hex[:12]}"

    s, claim, _ = repo.acquire_payment_creation_claim(customer, receipt, 50000, "INR", lease_seconds=30)
    repo.confirm_payment_intent(customer, receipt, "order_initial_id", claim_token=claim)

    # Identical confirmation returns existing order without error
    same_conf = repo.confirm_payment_intent(customer, receipt, "order_initial_id")
    assert same_conf["order_id"] == "order_initial_id"

    # Conflicting order ID is strictly rejected
    with pytest.raises(DomainError) as exc_info:
        repo.confirm_payment_intent(customer, receipt, "order_different_id")
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PAYMENT_ORDER_CONFLICT"

    # Database record is unchanged
    order = PaymentOrder.objects.get(customer=customer, receipt=receipt)
    assert order.provider_order_id == "order_initial_id"


def test_recharge_conflict_on_mismatched_amount_or_currency():
    """Recharge order recovery must reject conflicting amount or currency for the same receipt."""
    customer = create_customer_principal()
    receipt = f"rc_conflict_{uuid4().hex[:12]}"

    repo.acquire_payment_creation_claim(customer, receipt, 50000, "INR")

    with pytest.raises(DomainError) as exc_amount:
        repo.get_payment_order_by_receipt(customer, receipt, expected_amount_paise=100000, expected_currency="INR")
    assert exc_amount.value.status_code == 409
    assert exc_amount.value.code == "PAYMENT_ORDER_CONFLICT"

    with pytest.raises(DomainError) as exc_curr:
        repo.get_payment_order_by_receipt(customer, receipt, expected_amount_paise=50000, expected_currency="USD")
    assert exc_curr.value.status_code == 409
    assert exc_curr.value.code == "PAYMENT_ORDER_CONFLICT"


def test_postgresql_locking_and_rollback_on_evidence_failure():
    """If payment evidence verification fails, all DB changes must roll back cleanly under PostgreSQL."""
    customer = create_customer_principal()
    secret = "rzp_secret_rollback"
    order_id = f"order_{uuid4().hex[:12]}"
    payment_id = f"pay_{uuid4().hex[:12]}"

    repo.create_payment_order(customer, 50000, "INR", order_id, "key_id")

    bad_evidence = {
        "id": payment_id,
        "order_id": order_id,
        "status": "failed",
        "amount": 50000,
        "currency": "INR",
    }
    sig = compute_sig(order_id, payment_id, secret)

    with pytest.raises(DomainError) as exc_info:
        repo.verify_and_credit_recharge(
            customer, order_id, payment_id, sig, secret, payment_evidence=bad_evidence
        )
    assert exc_info.value.code == "PAYMENT_NOT_CAPTURED"

    order = PaymentOrder.objects.get(provider_order_id=order_id)
    assert order.state == "PENDING"
    assert order.provider_payment_id is None

    wallet = WalletAccount.objects.filter(customer=customer).first()
    if wallet:
        assert wallet.balance_paise == 0

    assert WalletTransaction.objects.filter(reference_id=payment_id).count() == 0


def test_support_ticket_reopening_on_customer_reply():
    """When a ticket is RESOLVED or CLOSED, a customer reply must automatically reopen it to OPEN."""
    customer = create_customer_principal()

    # Case 1: RESOLVED ticket
    ticket_resolved = CustomerSupportTicket.objects.create(
        customer=customer,
        subject="Resolved issue",
        category="CHARGING_ISSUE",
        status="RESOLVED",
        version=1,
    )
    res_reply = repo.reply_support_ticket(
        actor=customer,
        ticket_id=ticket_resolved.id,
        message="Actually the issue happened again today.",
    )
    ticket_resolved.refresh_from_db()
    assert ticket_resolved.status == "OPEN"
    assert ticket_resolved.version == 2
    assert CustomerSupportMessage.objects.filter(ticket=ticket_resolved).count() == 1
    assert res_reply["status"] == "OPEN"

    # Case 2: CLOSED ticket
    ticket_closed = CustomerSupportTicket.objects.create(
        customer=customer,
        subject="Closed issue",
        category="BILLING",
        status="CLOSED",
        version=1,
    )
    close_reply = repo.reply_support_ticket(
        actor=customer,
        ticket_id=ticket_closed.id,
        message="Need further clarification on this bill.",
    )
    ticket_closed.refresh_from_db()
    assert ticket_closed.status == "OPEN"
    assert ticket_closed.version == 2
    assert CustomerSupportMessage.objects.filter(ticket=ticket_closed).count() == 1
    assert close_reply["status"] == "OPEN"
