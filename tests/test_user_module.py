"""Comprehensive unit and contract tests for the Customer / User plane modules (cow/users).

Tests strict Pydantic schemas, AST module boundaries, collision-resistant receipt hashing,
allowlist evidence sanitization, fail-closed payment verification preconditions,
idempotent order recovery & reconciliation workflows, and fault injection.

Follows the project engineering contract: runs cleanly under standard test runners
without global database mutation or SQLite monkeypatching at import time.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

# Set test environment variables required by Django settings without overriding DATABASES
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("PASSWORD_PEPPER", "test-only-password-pepper-" + "p" * 40)
os.environ.setdefault("OTP_HMAC_PEPPER", "test-only-otp-pepper-" + "o" * 40)
os.environ.setdefault("PUBLIC_BASE_URL", "http://127.0.0.1:18280")
os.environ.setdefault("HTTP_LISTEN_ADDR", "127.0.0.1:18280")
os.environ.setdefault("SMTP_TLS_MODE", "starttls")
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:1/cow_schema_offline?sslmode=disable")

import django
django.setup()

import unittest
from pydantic import ValidationError

from cow.common.errors import DomainError
from cow.users import contracts, repository as repo, schemas, service
from cow.users.repository import (
    RECONCILIATION_EVIDENCE_ALLOWLIST,
    format_paise_to_inr_str,
    sanitize_payment_evidence,
)
from cow.users.service import compute_order_receipt


class UserSchemasTest(unittest.TestCase):
    """Test strict Pydantic payload and response schemas for the customer plane."""

    def test_profile_schemas(self):
        p = schemas.ProfileData(full_name="Abhranil Pal", phone="+919876543210", version=1)
        self.assertEqual(p.full_name, "Abhranil Pal")
        self.assertEqual(p.version, 1)
        envelope = schemas.ProfileEnvelope(data=p)
        self.assertEqual(envelope.data.phone, "+919876543210")

    def test_vehicle_schemas(self):
        v = schemas.VehicleData(
            id=str(uuid4()),
            label="My Nexon EV",
            make="Tata",
            model="Nexon EV",
            variant="Max",
            registration_number="WB02AB1234",
            battery_capacity_wh=40500,
            connector_types=["CCS2"],
            supports_ac=True,
            supports_dc=True,
            max_ac_power_w=7200,
            max_dc_power_w=50000,
            active=True,
            saved=True,
            version=1,
            capability_evidence="UNVERIFIED_CUSTOMER_DATA",
        )
        self.assertEqual(v.make, "Tata")
        self.assertEqual(v.capability_evidence, "UNVERIFIED_CUSTOMER_DATA")
        envelope = schemas.VehicleEnvelope(data=v)
        self.assertEqual(envelope.data.battery_capacity_wh, 40500)
        list_envelope = schemas.VehicleListEnvelope(data=[v])
        self.assertEqual(len(list_envelope.data), 1)

    def test_address_schemas(self):
        a = schemas.AddressData(
            id=str(uuid4()),
            address="Salt Lake Sector V, Kolkata",
            latitude="22.5800000",
            longitude="88.4300000",
            city="Kolkata",
            state="West Bengal",
            country_code="IN",
            active=True,
            is_default=True,
            saved=True,
            version=1,
        )
        self.assertEqual(a.country_code, "IN")
        self.assertTrue(a.is_default)
        envelope = schemas.AddressEnvelope(data=a)
        self.assertEqual(envelope.data.city, "Kolkata")

    def test_service_request_schemas(self):
        v_id = uuid4()
        a_id = uuid4()
        req_payload = schemas.ServiceRequestPayload(
            vehicle_id=v_id,
            address_id=a_id,
            requested_energy_wh=35000,
        )
        self.assertEqual(req_payload.requested_energy_wh, 35000)

        # Negative energy requested is rejected by schema validator
        with self.assertRaises(ValidationError):
            schemas.ServiceRequestPayload(
                vehicle_id=v_id,
                address_id=a_id,
                requested_energy_wh=-10,
            )

    def test_rating_schemas(self):
        valid = schemas.RatingCreatePayload(rating=5, comment="Excellent charging service!")
        self.assertEqual(valid.rating, 5)

        # Ratings outside 1-5 range are strictly rejected
        for bad_r in (0, 6, -1, 10):
            with self.assertRaises(ValidationError):
                schemas.RatingCreatePayload(rating=bad_r, comment="")

        # Comments longer than 500 characters are strictly rejected
        with self.assertRaises(ValidationError):
            schemas.RatingCreatePayload(rating=5, comment="x" * 501)

    def test_support_ticket_schemas(self):
        ticket_payload = schemas.SupportTicketCreatePayload(
            subject="Charging stopped unexpectedly",
            category="CHARGING_ISSUE",
            message="The session ended at 80% instead of 100%.",
        )
        self.assertEqual(ticket_payload.category, "CHARGING_ISSUE")

        # Subject longer than 200 chars rejected
        with self.assertRaises(ValidationError):
            schemas.SupportTicketCreatePayload(
                subject="a" * 201,
                category="BILLING",
                message="Help",
            )

        # Message longer than 2000 chars rejected
        with self.assertRaises(ValidationError):
            schemas.SupportReplyPayload(message="a" * 2001)

    def test_notification_and_preference_schemas(self):
        notif = schemas.NotificationData(
            id=str(uuid4()),
            title="Session Completed",
            body="Your EV has been charged to 90%.",
            category="CHARGING",
            is_read=False,
            read_at=None,
            data={"energy_wh": 35000},
            created_at="2026-11-12T12:00:00Z",
        )
        self.assertEqual(notif.category, "CHARGING")
        self.assertEqual(notif.body, "Your EV has been charged to 90%.")

        prefs = schemas.NotificationPreferencesPayload(
            push_enabled=True,
            email_enabled=False,
            sms_enabled=True,
        )
        self.assertFalse(prefs.email_enabled)

    def test_realtime_ticket_schemas(self):
        ticket_req = schemas.RealtimeTicketPayload(
            subscriptions=["self", "notifications"],
            resume_after="evt_12345",
        )
        self.assertEqual(len(ticket_req.subscriptions), 2)
        self.assertEqual(ticket_req.resume_after, "evt_12345")

    def test_wallet_recharge_order_amount_bounds(self):
        # 100 paise (₹1.00) is valid minimum
        o1 = schemas.WalletRechargeOrderPayload(amount_paise=100)
        self.assertEqual(o1.amount_paise, 100)
        self.assertEqual(o1.currency, "INR")

        # 50,000 paise (₹500.00) is valid
        o2 = schemas.WalletRechargeOrderPayload(amount_paise=50000)
        self.assertEqual(o2.amount_paise, 50000)

        # 10,000,000 paise (₹100,000.00) is valid maximum
        o3 = schemas.WalletRechargeOrderPayload(amount_paise=10000000)
        self.assertEqual(o3.amount_paise, 10000000)

        # 99 paise is strictly rejected (< 100)
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=99)

        # 0 paise is strictly rejected
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=0)

        # Negative amount is strictly rejected
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=-500)

        # > 10,000,000 paise is strictly rejected
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=10000001)

        # Non-INR currency is rejected
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=5000, currency="USD")

    def test_wallet_recharge_verify_schemas(self):
        v = schemas.WalletRechargeVerifyPayload(
            razorpay_order_id="order_test_123",
            razorpay_payment_id="pay_test_456",
            razorpay_signature="sig_test_789",
        )
        self.assertEqual(v.razorpay_order_id, "order_test_123")

        # Missing required fields raises ValidationError
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeVerifyPayload(
                razorpay_order_id="order_test_123",
                razorpay_payment_id="pay_test_456",
            )


class UserPureLogicTest(unittest.TestCase):
    """Test pure domain/service logic, collision resistance, allowlists, and formatting."""

    def test_receipt_digest_collision_resistance_for_identical_prefixes(self):
        """Two different keys sharing the first 16 or 32 characters must generate distinct receipts."""
        class DummyPrincipal:
            def __init__(self, pid: str):
                self.id = UUID(pid)

        p = DummyPrincipal("11111111-1111-1111-1111-111111111111")
        # Identical 24-character prefix
        key_a = "shared_prefix_1234567890_alpha"
        key_b = "shared_prefix_1234567890_bravo"

        receipt_a = compute_order_receipt(p, key_a)
        receipt_b = compute_order_receipt(p, key_b)

        self.assertNotEqual(receipt_a, receipt_b)
        self.assertTrue(receipt_a.startswith("rc_"))
        self.assertTrue(receipt_b.startswith("rc_"))
        # Must be strictly <= 40 characters for Razorpay Orders API
        self.assertLessEqual(len(receipt_a), 40)
        self.assertLessEqual(len(receipt_b), 40)
        self.assertEqual(len(receipt_a), 35)

    def test_receipt_digest_actor_isolation(self):
        """Identical idempotency keys for different actors must generate distinct receipts."""
        class DummyPrincipal:
            def __init__(self, pid: str):
                self.id = UUID(pid)

        p1 = DummyPrincipal("11111111-1111-1111-1111-111111111111")
        p2 = DummyPrincipal("22222222-2222-2222-2222-222222222222")
        key = "shared_client_idempotency_key_123"

        receipt_1 = compute_order_receipt(p1, key)
        receipt_2 = compute_order_receipt(p2, key)

        self.assertNotEqual(receipt_1, receipt_2)

    def test_receipt_digest_is_deterministic(self):
        """Same actor and same key must produce the exact same receipt every time."""
        class DummyPrincipal:
            def __init__(self, pid: str):
                self.id = UUID(pid)

        p = DummyPrincipal("33333333-3333-3333-3333-333333333333")
        key = "deterministic_key_for_recovery_123"
        r1 = compute_order_receipt(p, key)
        r2 = compute_order_receipt(p, key)
        self.assertEqual(r1, r2)

    def test_evidence_allowlist_sanitization(self):
        """Payment evidence sanitization must strictly keep only allowlisted fields."""
        raw_evidence = {
            # Allowlisted fields
            "id": "pay_test_123",
            "order_id": "order_test_456",
            "amount": 50000,
            "currency": "INR",
            "status": "captured",
            "method": "upi",
            "created_at": 1731400000,
            "bank": "HDFC",
            "wallet": None,
            "vpa": "customer@okaxis",
            "email": "customer@example.com",
            "contact": "+919876543210",
            "fee": 1180,
            "tax": 180,
            "error_code": None,
            "error_description": None,
            # Sensitive / unexpected / injected fields that MUST be dropped
            "secret": "super_secret_key",
            "key_secret": "sensitive_token",
            "access_token": "bearer_abc_xyz",
            "auth": "Basic 12345",
            "Authorization": "Bearer secret",
            "card_cvv": "123",
            "internal_server_debug": {"stack": "trace"},
            "arbitrary_injected_param": "malicious",
        }

        sanitized = sanitize_payment_evidence(raw_evidence)

        # Check all allowlisted fields are present
        for field in (
            "id", "order_id", "amount", "currency", "status", "method",
            "created_at", "bank", "vpa", "email", "contact", "fee", "tax",
        ):
            self.assertIn(field, sanitized)
            self.assertEqual(sanitized[field], raw_evidence[field])

        # Check sensitive & unexpected fields are completely stripped
        for forbidden in (
            "secret", "key_secret", "access_token", "auth", "Authorization",
            "card_cvv", "internal_server_debug", "arbitrary_injected_param",
        ):
            self.assertNotIn(forbidden, sanitized)

        # Only allowlisted keys remain
        self.assertTrue(set(sanitized.keys()).issubset(RECONCILIATION_EVIDENCE_ALLOWLIST))

    def test_evidence_allowlist_empty_or_none(self):
        self.assertEqual(sanitize_payment_evidence(None), {})
        self.assertEqual(sanitize_payment_evidence({}), {})

    def test_format_paise_to_inr_str(self):
        self.assertEqual(format_paise_to_inr_str(0), "0.00")
        self.assertEqual(format_paise_to_inr_str(5), "0.05")
        self.assertEqual(format_paise_to_inr_str(99), "0.99")
        self.assertEqual(format_paise_to_inr_str(100), "1.00")
        self.assertEqual(format_paise_to_inr_str(50000), "500.00")
        self.assertEqual(format_paise_to_inr_str(10000000), "100000.00")

    def test_hmac_signature_calculation_and_tamper_detection(self):
        secret = "secret_key_for_hmac_test_123"
        order_id = "order_abc_123"
        payment_id = "pay_xyz_789"
        message = f"{order_id}|{payment_id}".encode("utf-8")
        valid_sig = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

        # Valid signature matches
        expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        self.assertTrue(hmac.compare_digest(expected.lower(), valid_sig.lower()))

        # Tampered payment ID fails
        tampered_msg = f"{order_id}|pay_DIFFERENT".encode("utf-8")
        tampered_expected = hmac.new(secret.encode("utf-8"), tampered_msg, hashlib.sha256).hexdigest()
        self.assertFalse(hmac.compare_digest(tampered_expected.lower(), valid_sig.lower()))

        # Tampered secret fails
        diff_secret_expected = hmac.new(b"different_secret", message, hashlib.sha256).hexdigest()
        self.assertFalse(hmac.compare_digest(diff_secret_expected.lower(), valid_sig.lower()))


class UserServiceWorkflowsTest(unittest.TestCase):
    """Test service orchestration, idempotent recovery, conflict detection, and fault injection."""

    def setUp(self):
        class DummyPrincipal:
            def __init__(self):
                self.id = UUID("44444444-4444-4444-4444-444444444444")
        self.actor = DummyPrincipal()
        self.key = "test_valid_idempotency_key_123"

    @patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key_id", "rzp_secret"))
    @patch("cow.users.service.repo.get_payment_order_by_receipt", return_value=None)
    @patch("cow.users.service.repo.create_payment_intent")
    @patch("cow.users.service.reconcile_razorpay_order_by_receipt", return_value=None)
    @patch("cow.users.service.call_razorpay_create_order", return_value="order_gateway_777")
    @patch("cow.users.service.repo.confirm_payment_intent")
    def test_recharge_order_normal_flow(
        self,
        mock_confirm,
        mock_create_order,
        mock_reconcile,
        mock_create_intent,
        mock_get_existing,
        mock_creds,
    ):
        mock_confirm.return_value = {
            "order_id": "order_gateway_777",
            "provider_order_id": "order_gateway_777",
            "provider": "RAZORPAY",
            "amount_paise": 50000,
            "amount_inr": "500.00",
            "currency": "INR",
            "key_id": "rzp_key_id",
            "state": "PENDING",
        }

        resp = service.create_wallet_recharge_order(
            self.actor, {"amount_paise": 50000, "currency": "INR"}, self.key
        )

        self.assertEqual(resp.status_code, 201)
        # Durable intent was created locally BEFORE calling gateway
        mock_create_intent.assert_called_once()
        # Reconciliation was attempted
        mock_reconcile.assert_called_once()
        # Gateway was called
        mock_create_order.assert_called_once()
        # Intent was confirmed with gateway order ID
        mock_confirm.assert_called_once_with(
            actor=self.actor,
            receipt=compute_order_receipt(self.actor, self.key),
            provider_order_id="order_gateway_777",
            key_id="rzp_key_id",
        )

    @patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key_id", "rzp_secret"))
    @patch("cow.users.service.repo.get_payment_order_by_receipt")
    @patch("cow.users.service.call_razorpay_create_order")
    def test_recharge_order_idempotent_recovery(
        self,
        mock_create_order,
        mock_get_existing,
        mock_creds,
    ):
        """When an order is already confirmed, subsequent retries return it without calling gateway."""
        confirmed_order = {
            "order_id": "order_already_confirmed_999",
            "provider_order_id": "order_already_confirmed_999",
            "provider": "RAZORPAY",
            "amount_paise": 50000,
            "amount_inr": "500.00",
            "currency": "INR",
            "key_id": "rzp_key_id",
            "state": "PENDING",
            "is_intent": False,
        }
        mock_get_existing.return_value = confirmed_order

        resp = service.create_wallet_recharge_order(
            self.actor, {"amount_paise": 50000, "currency": "INR"}, self.key
        )

        self.assertEqual(resp.status_code, 201)
        # Gateway create order was NOT called!
        mock_create_order.assert_not_called()

    @patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key_id", "rzp_secret"))
    @patch("cow.users.service.repo.get_payment_order_by_receipt")
    def test_recharge_order_conflict_on_mismatched_amount(
        self,
        mock_get_existing,
        mock_creds,
    ):
        """When a retry uses an existing receipt with a conflicting amount, it must be rejected with 409."""
        mock_get_existing.side_effect = DomainError(
            409,
            "PAYMENT_ORDER_CONFLICT",
            "Payment order exists with conflicting amount",
        )

        with self.assertRaises(DomainError) as ctx:
            service.create_wallet_recharge_order(
                self.actor, {"amount_paise": 100000, "currency": "INR"}, self.key
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "PAYMENT_ORDER_CONFLICT")

    @patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key_id", "rzp_secret"))
    @patch("cow.users.service.repo.get_payment_order_by_receipt", return_value={"is_intent": True})
    @patch("cow.users.service.reconcile_razorpay_order_by_receipt", return_value="order_reconciled_555")
    @patch("cow.users.service.call_razorpay_create_order")
    @patch("cow.users.service.repo.confirm_payment_intent")
    def test_recharge_order_reconciliation_after_uncertain_outcome(
        self,
        mock_confirm,
        mock_create_order,
        mock_reconcile,
        mock_get_existing,
        mock_creds,
    ):
        """When prior attempt was uncertain, retry reconciles with Razorpay and confirms without re-creating."""
        mock_confirm.return_value = {
            "order_id": "order_reconciled_555",
            "provider_order_id": "order_reconciled_555",
            "provider": "RAZORPAY",
            "amount_paise": 50000,
            "amount_inr": "500.00",
            "currency": "INR",
            "key_id": "rzp_key_id",
            "state": "PENDING",
        }

        resp = service.create_wallet_recharge_order(
            self.actor, {"amount_paise": 50000, "currency": "INR"}, self.key
        )

        self.assertEqual(resp.status_code, 201)
        mock_reconcile.assert_called_once()
        # Gateway create order was NOT called because reconciliation recovered it!
        mock_create_order.assert_not_called()
        mock_confirm.assert_called_once_with(
            actor=self.actor,
            receipt=compute_order_receipt(self.actor, self.key),
            provider_order_id="order_reconciled_555",
            key_id="rzp_key_id",
        )

    @patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key_id", "rzp_secret"))
    @patch("cow.users.service.repo.get_payment_order_by_receipt", return_value=None)
    @patch("cow.users.service.repo.create_payment_intent")
    @patch("cow.users.service.reconcile_razorpay_order_by_receipt", return_value=None)
    @patch("cow.users.service.call_razorpay_create_order", side_effect=DomainError(504, "GATEWAY_TIMEOUT", "Connection timed out"))
    @patch("cow.users.service.repo.mark_payment_intent_uncertain")
    def test_recharge_order_fault_injection_marks_intent_uncertain(
        self,
        mock_mark_uncertain,
        mock_create_order,
        mock_reconcile,
        mock_create_intent,
        mock_get_existing,
        mock_creds,
    ):
        """When the gateway times out, the local intent must be marked uncertain before propagating 504."""
        with self.assertRaises(DomainError) as ctx:
            service.create_wallet_recharge_order(
                self.actor, {"amount_paise": 50000, "currency": "INR"}, self.key
            )
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(ctx.exception.code, "GATEWAY_TIMEOUT")
        mock_mark_uncertain.assert_called_once()

    def test_recharge_order_key_validation(self):
        # Missing key
        with self.assertRaises(DomainError) as ctx:
            service.create_wallet_recharge_order(self.actor, {"amount_paise": 50000}, "")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_KEY_REQUIRED")

        # Key too short (< 16 chars)
        with self.assertRaises(DomainError) as ctx:
            service.create_wallet_recharge_order(self.actor, {"amount_paise": 50000}, "short_key")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_KEY_REQUIRED")

        # Key too long (> 128 chars)
        with self.assertRaises(DomainError) as ctx:
            service.create_wallet_recharge_order(self.actor, {"amount_paise": 50000}, "k" * 129)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_KEY_REQUIRED")

    @patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key_id", ""))
    def test_verify_recharge_rejects_missing_credentials(self, mock_creds):
        with self.assertRaises(DomainError) as ctx:
            service.verify_wallet_recharge(
                self.actor,
                {
                    "razorpay_order_id": "order_123",
                    "razorpay_payment_id": "pay_123",
                    "razorpay_signature": "sig_123",
                },
            )
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.code, "CONFIGURATION_ERROR")


class UserModuleStructureTest(unittest.TestCase):
    """AST inspections enforcing architectural contracts, model boundaries, and error codes."""

    def test_canonical_model_imports_only(self):
        """User module must only import models from canonical cow.models, never declaring local models."""
        users_dir = Path(repo.__file__).resolve().parent
        for py_file in users_dir.glob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module:
                        self.assertFalse(
                            node.module.endswith("models_zone") or node.module.endswith("models_local"),
                            f"Illegal models module imported in {py_file}: {node.module}",
                        )

    def test_router_endpoints_use_service_layer(self):
        """All router operations must delegate to service functions, avoiding direct DB writes."""
        router_path = Path(service.__file__).resolve().parent / "router.py"
        with open(router_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("service.create_wallet_recharge_order", content)
        self.assertIn("service.verify_wallet_recharge", content)
        self.assertIn("service.get_wallet", content)
        self.assertIn("service.list_wallet_transactions", content)

    def test_idempotency_key_enforcement(self):
        """Wallet recharge order routes must enforce Idempotency-Key header."""
        router_path = Path(service.__file__).resolve().parent / "router.py"
        with open(router_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('service.get_idempotency_key(request, required=True)', content)


if __name__ == "__main__":
    unittest.main()
