"""Comprehensive unit and integration tests for the Customer / User plane modules (cow/users).

Tests strict Pydantic schemas, service layer precondition/header validation,
isolated database transactions in SQLite, fail-closed payment verification,
concurrency/idempotency behavior, support reopening, rating preconditions,
and notification delivery preferences.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

# Configure isolated in-memory test environment without loading real .env
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["PASSWORD_PEPPER"] = "a" * 32
os.environ["OTP_HMAC_PEPPER"] = "b" * 32
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["HTTP_LISTEN_ADDR"] = "127.0.0.1:8000"
os.environ["SMTP_TLS_MODE"] = "tls"
os.environ["DATABASE_URL"] = "postgresql://app:pass@127.0.0.1:5432/evcowbbe_test?sslmode=disable"

from django.conf import settings

# Route DB operations to SQLite in-memory to prevent hitting any external PostgreSQL / Aiven development DB
settings.DATABASES["default"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": ":memory:",
    "ATOMIC_REQUESTS": False,
    "AUTOCOMMIT": True,
    "CONN_MAX_AGE": 0,
    "CONN_HEALTH_CHECKS": False,
    "OPTIONS": {},
    "TIME_ZONE": None,
    "USER": "",
    "PASSWORD": "",
    "HOST": "",
    "PORT": "",
    "TEST": {},
}

import django

django.setup()

from django.db import connection
from django.db.backends.signals import connection_created
from django.dispatch import receiver
from django.test import Client
from django.utils import timezone
from pydantic import ValidationError

from cow.common.errors import DomainError
from cow.models import (
    Account,
    AuditEvent,
    ChargingSession,
    CustomerAddress,
    CustomerJobRating,
    CustomerNotification,
    CustomerNotificationPreference,
    CustomerProfile,
    CustomerSupportMessage,
    CustomerSupportTicket,
    CustomerVehicle,
    MeterObservation,
    PaymentOrder,
    PricingPolicy,
    Principal,
    Quote,
    ServiceJob,
    ServiceRequest,
    WalletAccount,
    WalletTransaction,
)
from cow.users import contracts, repository as repo, schemas, service


@receiver(connection_created)
def extend_sqlite(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        connection.connection.create_function("pg_advisory_xact_lock", 1, lambda x: None)


_TABLES_CREATED = False


def create_schema_tables() -> None:
    """Create test tables in SQLite in-memory database once."""
    global _TABLES_CREATED
    if _TABLES_CREATED:
        return
    connection.ensure_connection()
    extend_sqlite(None, connection)
    from django.apps import apps
    all_models = list(apps.get_app_config("cow").get_models())
    with connection.schema_editor() as editor:
        for m in all_models:
            editor.create_model(m)
    _TABLES_CREATED = True


def create_test_customer(email_prefix: str = "cust") -> Principal:
    """Helper to create a valid customer principal and profile."""
    uid = uuid4().hex[:8]
    acct = Account.objects.create(email=f"{email_prefix}_{uid}@example.com", password_hash="dummy_hash")
    princ = Principal.objects.create(account=acct, plane="app", status="ACTIVE")
    CustomerProfile.objects.create(principal=princ, full_name=f"Customer {uid}", phone="+919876543210")
    return princ


class UserSchemasTest(unittest.TestCase):
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

        with self.assertRaises(ValidationError):
            schemas.ServiceRequestPayload(
                vehicle_id=v_id,
                address_id=a_id,
                requested_energy_wh=0,
            )
        with self.assertRaises(ValidationError):
            schemas.ServiceRequestPayload(
                vehicle_id=v_id,
                address_id=a_id,
                requested_energy_wh=600000,
            )

    def test_job_schemas(self):
        j_id = str(uuid4())
        job_data = schemas.JobDetailData(
            id=j_id,
            state="DISPATCHING",
            version=1,
            vehicle_snapshot={"make": "Tata", "model": "Nexon EV"},
            address_snapshot={"address": "Kolkata"},
            requested_energy_wh=35000,
            quoted_total_paise=50000,
            currency="INR",
        )
        self.assertEqual(job_data.state, "DISPATCHING")
        self.assertEqual(job_data.quoted_total_paise, 50000)

        cancel_payload = schemas.JobCancelPayload(reason="Changed plans")
        self.assertEqual(cancel_payload.reason, "Changed plans")

        live_data = schemas.JobLiveProjectionData(
            job_id=j_id,
            state="EN_ROUTE",
            eta_minutes=None,
            freshness_timestamp="2026-11-12T10:00:00Z",
        )
        self.assertIsNone(live_data.eta_minutes)

    def test_rating_schemas(self):
        r = schemas.RatingCreatePayload(rating=5, comment="Great service!")
        self.assertEqual(r.rating, 5)

        with self.assertRaises(ValidationError):
            schemas.RatingCreatePayload(rating=0)
        with self.assertRaises(ValidationError):
            schemas.RatingCreatePayload(rating=6)

    def test_support_schemas(self):
        ticket_req = schemas.SupportTicketCreatePayload(
            category="CHARGING_ISSUE",
            subject="Charging stopped unexpectedly",
            message="The session ended at 40% instead of 80%.",
        )
        self.assertEqual(ticket_req.category, "CHARGING_ISSUE")

        reply_req = schemas.SupportReplyPayload(message="Here is more information.")
        self.assertEqual(reply_req.message, "Here is more information.")

    def test_notification_schemas(self):
        notif = schemas.NotificationData(
            id=str(uuid4()),
            title="Service Confirmed",
            body="Driver is en route",
            category="SERVICE",
            is_read=False,
            read_at=None,
            created_at="2026-11-12T10:00:00Z",
            data={"job_id": "test_job"},
        )
        self.assertFalse(notif.is_read)
        self.assertEqual(notif.body, "Driver is en route")
        self.assertEqual(notif.category, "SERVICE")

        prefs = schemas.NotificationPreferencesData(
            push_enabled=True,
            email_enabled=True,
            sms_enabled=False,
            service_updates=True,
            promotions=False,
        )
        self.assertFalse(prefs.sms_enabled)

    def test_realtime_schemas(self):
        rt = schemas.RealtimeTicketPayload(
            subscriptions=["self", "notifications"],
        )
        self.assertIn("self", rt.subscriptions)

    def test_wallet_schemas(self):
        w = schemas.WalletAccountData(
            balance_paise=50000,
            balance_inr="500.00",
            currency="INR",
            version=1,
        )
        self.assertEqual(w.balance_paise, 50000)
        self.assertEqual(w.balance_inr, "500.00")

        tx = schemas.WalletTransactionData(
            id=str(uuid4()),
            transaction_type="CREDIT_TOPUP",
            amount_paise=50000,
            amount_inr="500.00",
            balance_after_paise=50000,
            balance_after_inr="500.00",
            reference_id="pay_12345",
        )
        self.assertEqual(tx.transaction_type, "CREDIT_TOPUP")

        order_req = schemas.WalletRechargeOrderPayload(amount_paise=10000)
        self.assertEqual(order_req.amount_paise, 10000)
        self.assertEqual(order_req.currency, "INR")

        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=0)
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=99)
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=10000001)
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=5000, currency="USD")

        verify_req = schemas.WalletRechargeVerifyPayload(
            razorpay_order_id="order_123",
            razorpay_payment_id="pay_456",
            razorpay_signature="abcde12345",
        )
        self.assertEqual(verify_req.razorpay_order_id, "order_123")

    def test_paise_to_inr_string_exact_formatting(self):
        self.assertEqual(schemas.format_paise_to_inr_str(0), "0.00")
        self.assertEqual(schemas.format_paise_to_inr_str(5), "0.05")
        self.assertEqual(schemas.format_paise_to_inr_str(99), "0.99")
        self.assertEqual(schemas.format_paise_to_inr_str(100), "1.00")
        self.assertEqual(schemas.format_paise_to_inr_str(50000), "500.00")
        self.assertEqual(schemas.format_paise_to_inr_str(1234567), "12345.67")
        self.assertEqual(schemas.format_paise_to_inr_str(-250), "-2.50")


class UserModuleStructureTest(unittest.TestCase):
    def test_all_user_files_parse(self):
        user_dir = ROOT / "cow/users"
        for py_file in user_dir.glob("*.py"):
            with self.subTest(file=py_file.name):
                ast.parse(py_file.read_text(encoding="utf-8"))

    def test_no_forbidden_class_names(self):
        user_dir = ROOT / "cow/users"
        forbidden = ("Account", "PricingPolicy", "ServiceZone", "ServiceJob")
        for py_file in user_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    self.assertNotIn(node.name, forbidden, f"{py_file.name} defines {node.name}")

    def test_router_operation_ids_registered(self):
        router_src = (ROOT / "cow/users/router.py").read_text(encoding="utf-8")
        charging_src = (ROOT / "cow/users/charging.py").read_text(encoding="utf-8")
        master_src = (ROOT / "cow/users/master.py").read_text(encoding="utf-8")

        for op in ("cow_api_request_service", "cow_api_quote_request", "cow_api_accept_quote", "cow_api_get_job"):
            self.assertIn(f'operation_id="{op}"', charging_src)

        expected_master = (
            "app_profile_get", "app_profile_patch",
            "app_vehicles_list", "app_vehicles_create", "app_vehicles_get", "app_vehicles_patch", "app_vehicles_delete",
            "app_addresses_list", "app_addresses_create", "app_addresses_get", "app_addresses_patch", "app_addresses_delete",
            "app_addresses_default",
        )
        for op in expected_master:
            self.assertIn(f'operation_id="{op}"', master_src)

        expected_extended = (
            "app_service_requests_list",
            "app_service_request_get",
            "app_jobs_list",
            "app_jobs_cancel",
            "app_jobs_live",
            "app_jobs_promote_vehicle",
            "app_jobs_promote_address",
            "app_ratings_list",
            "app_jobs_rating_get",
            "app_jobs_rating_put",
            "app_support_tickets_list",
            "app_support_tickets_create",
            "app_support_tickets_get",
            "app_support_tickets_reply",
            "app_notifications_list",
            "app_notifications_unread_count",
            "app_notifications_read",
            "app_notifications_read_all",
            "app_notifications_preferences_get",
            "app_notifications_preferences_put",
            "app_realtime_tickets_create",
            "app_wallet_get",
            "app_wallet_transactions_list",
            "app_wallet_recharge_order",
            "app_wallet_payment_order",
            "app_wallet_recharge_verify",
            "app_wallet_verify_payment",
        )
        for op in expected_extended:
            self.assertIn(f'operation_id="{op}"', router_src)

    def test_all_user_models_declared(self):
        models_src = (ROOT / "cow/models.py").read_text(encoding="utf-8")
        self.assertIn("class WalletAccount(Timestamped):", models_src)
        self.assertIn("class WalletTransaction(Timestamped):", models_src)
        self.assertIn("class PaymentOrder(Timestamped):", models_src)
        self.assertIn("class CustomerJobRating(Timestamped):", models_src)
        self.assertIn("class CustomerSupportTicket(Timestamped):", models_src)
        self.assertIn("class CustomerSupportMessage(Timestamped):", models_src)
        self.assertIn("class CustomerNotification(Timestamped):", models_src)
        self.assertIn("class CustomerNotificationPreference(Timestamped):", models_src)
        self.assertIn("cow_wallet_balance_nonnegative", models_src)
        self.assertIn("cow_pay_provider_payment_unique", models_src)
        self.assertIn("cow_job_rating_range_1_to_5", models_src)

    def test_realtime_ticket_fails_closed_when_not_configured(self):
        class DummyPrincipal:
            id = uuid4()

        with self.assertRaises(DomainError) as ctx:
            repo.create_realtime_ticket(DummyPrincipal(), ["self"])
        self.assertEqual(ctx.exception.code, "REALTIME_STREAM_NOT_CONFIGURED")
        self.assertEqual(ctx.exception.status_code, 501)


class UserIsolatedDBTest(unittest.TestCase):
    """Integration and concurrency tests executing against an isolated in-memory database."""

    @classmethod
    def setUpClass(cls):
        create_schema_tables()

    def setUp(self):
        self.customer1 = create_test_customer("cust1")
        self.customer2 = create_test_customer("cust2")
        self.secret = "WvCG1TeQLOABInHqBgwyWb2t"

    def _compute_sig(self, order_id: str, payment_id: str) -> str:
        data = f"{order_id}|{payment_id}".encode("utf-8")
        return hmac.new(self.secret.encode("utf-8"), data, hashlib.sha256).hexdigest()

    # ========================================================================
    # 1. Payment Verification Fail-Closed Tests
    # ========================================================================

    def test_verify_recharge_rejects_missing_or_empty_evidence(self):
        order = repo.create_payment_order(self.customer1, 50000, "INR", "order_fail_1", "key_1")
        sig = self._compute_sig(order["provider_order_id"], "pay_1")

        # Missing evidence
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(
                self.customer1, order["provider_order_id"], "pay_1", sig, self.secret, payment_evidence=None
            )
        self.assertEqual(ctx.exception.code, "MISSING_PAYMENT_EVIDENCE")

        # Empty dict evidence
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(
                self.customer1, order["provider_order_id"], "pay_1", sig, self.secret, payment_evidence={}
            )
        self.assertEqual(ctx.exception.code, "MISSING_PAYMENT_EVIDENCE")

    def test_verify_recharge_rejects_non_captured_status(self):
        order = repo.create_payment_order(self.customer1, 50000, "INR", "order_fail_2", "key_1")
        sig = self._compute_sig(order["provider_order_id"], "pay_2")

        for bad_status in ("failed", "authorized", "created", "refunded"):
            ev = {
                "id": "pay_2",
                "order_id": order["provider_order_id"],
                "status": bad_status,
                "amount": 50000,
                "currency": "INR",
            }
            with self.assertRaises(DomainError) as ctx:
                repo.verify_and_credit_recharge(
                    self.customer1, order["provider_order_id"], "pay_2", sig, self.secret, payment_evidence=ev
                )
            self.assertEqual(ctx.exception.code, "PAYMENT_NOT_CAPTURED")

    def test_verify_recharge_rejects_mismatches(self):
        order = repo.create_payment_order(self.customer1, 50000, "INR", "order_fail_3", "key_1")
        sig = self._compute_sig(order["provider_order_id"], "pay_3")

        # Payment ID mismatch
        ev = {"id": "pay_mismatch", "order_id": order["provider_order_id"], "status": "captured", "amount": 50000, "currency": "INR"}
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(self.customer1, order["provider_order_id"], "pay_3", sig, self.secret, ev)
        self.assertEqual(ctx.exception.code, "PAYMENT_ID_MISMATCH")

        # Order ID mismatch
        ev = {"id": "pay_3", "order_id": "order_mismatch", "status": "captured", "amount": 50000, "currency": "INR"}
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(self.customer1, order["provider_order_id"], "pay_3", sig, self.secret, ev)
        self.assertEqual(ctx.exception.code, "PAYMENT_ORDER_MISMATCH")

        # Amount mismatch
        ev = {"id": "pay_3", "order_id": order["provider_order_id"], "status": "captured", "amount": 40000, "currency": "INR"}
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(self.customer1, order["provider_order_id"], "pay_3", sig, self.secret, ev)
        self.assertEqual(ctx.exception.code, "PAYMENT_AMOUNT_MISMATCH")

        # Currency mismatch
        ev = {"id": "pay_3", "order_id": order["provider_order_id"], "status": "captured", "amount": 50000, "currency": "USD"}
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(self.customer1, order["provider_order_id"], "pay_3", sig, self.secret, ev)
        self.assertEqual(ctx.exception.code, "PAYMENT_CURRENCY_MISMATCH")

    def test_verify_recharge_rejects_simulated_evidence_in_real_flow(self):
        order = repo.create_payment_order(self.customer1, 50000, "INR", "order_fail_sim", "key_1")
        sig = self._compute_sig(order["provider_order_id"], "pay_sim")
        ev = {
            "id": "pay_sim",
            "order_id": order["provider_order_id"],
            "status": "captured",
            "amount": 50000,
            "currency": "INR",
            "simulated": True,
        }
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(self.customer1, order["provider_order_id"], "pay_sim", sig, self.secret, ev)
        self.assertEqual(ctx.exception.code, "PAYMENT_SIMULATION_DISALLOWED")

    def test_verify_recharge_successful_credit_and_safe_idempotent_replay(self):
        order = repo.create_payment_order(self.customer1, 50000, "INR", "order_succ_1", "key_1")
        pay_id = f"pay_{uuid4().hex[:12]}"
        sig = self._compute_sig(order["provider_order_id"], pay_id)
        ev = {
            "id": pay_id,
            "order_id": order["provider_order_id"],
            "status": "captured",
            "amount": 50000,
            "currency": "INR",
            "method": "upi",
            "key_secret": "sensitive_leaked_secret",  # Must be stripped from stored details
        }

        res = repo.verify_and_credit_recharge(self.customer1, order["provider_order_id"], pay_id, sig, self.secret, ev)
        self.assertEqual(res["status"], "CAPTURED")
        self.assertEqual(res["credited_paise"], 50000)
        self.assertEqual(res["credited_inr"], "500.00")
        self.assertEqual(res["balance_paise"], 50000)
        self.assertEqual(res["balance_inr"], "500.00")
        self.assertEqual(res["version"], 2)

        # Check DB state
        order_obj = PaymentOrder.objects.get(provider_order_id=order["provider_order_id"])
        self.assertEqual(order_obj.state, "CAPTURED")
        self.assertEqual(order_obj.provider_payment_id, pay_id)
        self.assertNotIn("key_secret", order_obj.details)

        # Ledger transaction appended
        tx = WalletTransaction.objects.filter(reference_id=pay_id).first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount_paise, 50000)
        self.assertEqual(tx.balance_after_paise, 50000)
        self.assertNotIn("key_secret", tx.details)

        # Idempotent replay: calling verify with identical credentials returns state without duplicate credit
        replay_res = repo.verify_and_credit_recharge(
            self.customer1, order["provider_order_id"], pay_id, sig, self.secret, ev
        )
        self.assertEqual(replay_res["status"], "CAPTURED")
        self.assertEqual(replay_res["balance_paise"], 50000)
        self.assertEqual(replay_res["version"], 2)  # Version NOT bumped on replay

        # Check that wallet still has 50000 paise (not 100000)
        w = WalletAccount.objects.get(customer=self.customer1)
        self.assertEqual(w.balance_paise, 50000)

    def test_verify_recharge_rejects_duplicate_payment_id_across_orders(self):
        order1 = repo.create_payment_order(self.customer1, 50000, "INR", "order_dup_1", "key_1")
        pay_id = f"pay_{uuid4().hex[:12]}"
        sig1 = self._compute_sig(order1["provider_order_id"], pay_id)
        ev1 = {"id": pay_id, "order_id": order1["provider_order_id"], "status": "captured", "amount": 50000, "currency": "INR"}
        repo.verify_and_credit_recharge(self.customer1, order1["provider_order_id"], pay_id, sig1, self.secret, ev1)

        # Attempt to reuse pay_id on order 2
        order2 = repo.create_payment_order(self.customer1, 50000, "INR", "order_dup_2", "key_1")
        sig2 = self._compute_sig(order2["provider_order_id"], pay_id)
        ev2 = {"id": pay_id, "order_id": order2["provider_order_id"], "status": "captured", "amount": 50000, "currency": "INR"}
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(self.customer1, order2["provider_order_id"], pay_id, sig2, self.secret, ev2)
        self.assertEqual(ctx.exception.code, "DUPLICATE_PAYMENT")

    # ========================================================================
    # 2. Razorpay Gateway Isolation & Order Recovery Tests
    # ========================================================================

    def test_gateway_clients_fail_closed_without_credentials(self):
        with self.assertRaises(DomainError) as ctx:
            service.call_razorpay_create_order("", "", 50000, "INR", "rc_test")
        self.assertEqual(ctx.exception.code, "CONFIGURATION_ERROR")
        self.assertEqual(ctx.exception.status_code, 500)

        with self.assertRaises(DomainError) as ctx:
            service.fetch_razorpay_payment("", "", "pay_test")
        self.assertEqual(ctx.exception.code, "CONFIGURATION_ERROR")
        self.assertEqual(ctx.exception.status_code, 500)

    @patch("cow.users.service.call_razorpay_create_order")
    def test_recharge_order_creation_idempotency_and_recovery(self, mock_create):
        mock_create.return_value = "order_rzp_mock_123"
        key = "idemp_order_create_001"

        with patch("cow.users.service.get_razorpay_credentials", return_value=("rzp_key", "rzp_sec")):
            res1 = service.create_wallet_recharge_order(self.customer1, {"amount_paise": 50000, "currency": "INR"}, key)
            self.assertEqual(res1.status_code, 201)
            self.assertEqual(mock_create.call_count, 1)

            # Replaying with same key should recover existing order without calling gateway again
            res2 = service.create_wallet_recharge_order(self.customer1, {"amount_paise": 50000, "currency": "INR"}, key)
            self.assertEqual(res2.status_code, 201)
            self.assertEqual(mock_create.call_count, 1)

    # ========================================================================
    # 3. Support Ticket Reopening & Isolation Tests
    # ========================================================================

    def test_reply_to_closed_or_resolved_ticket_reopens_to_open(self):
        ticket = CustomerSupportTicket.objects.create(
            customer=self.customer1,
            subject="Billing discrepancy",
            category="BILLING",
            status="RESOLVED",
            version=1,
        )
        CustomerSupportMessage.objects.create(ticket=ticket, sender=self.customer1, message="Initial problem")

        # Customer replies to RESOLVED ticket
        res = repo.reply_support_ticket(self.customer1, ticket.id, "The problem persists")
        self.assertEqual(res["status"], "OPEN")
        self.assertEqual(res["version"], 2)
        self.assertEqual(len(res["messages"]), 2)

        # Mark CLOSED
        ticket.refresh_from_db()
        ticket.status = "CLOSED"
        ticket.save()

        # Customer replies to CLOSED ticket
        res2 = repo.reply_support_ticket(self.customer1, ticket.id, "Need to reopen this")
        self.assertEqual(res2["status"], "OPEN")
        self.assertEqual(res2["version"], 3)
        self.assertEqual(len(res2["messages"]), 3)

    def test_support_ticket_ownership_isolation(self):
        ticket = CustomerSupportTicket.objects.create(
            customer=self.customer1,
            subject="Private ticket",
            category="GENERAL",
            status="OPEN",
            version=1,
        )
        # Customer 2 cannot read Customer 1's ticket
        with self.assertRaises(DomainError) as ctx:
            repo.get_support_ticket(self.customer2, ticket.id)
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

        # Customer 2 cannot reply to Customer 1's ticket
        with self.assertRaises(DomainError) as ctx:
            repo.reply_support_ticket(self.customer2, ticket.id, "Unauthorized reply")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    # ========================================================================
    # 4. Rating PUT Retries, Preconditions & Versioning Tests
    # ========================================================================

    def _create_completed_job(self, customer: Principal) -> ServiceJob:
        req = ServiceRequest.objects.create(
            customer=customer,
            vehicle_snapshot={"make": "Tata"},
            address_snapshot={"address": "Kolkata"},
            requested_energy_wh=30000,
            state="FULFILLED",
        )
        policy, _ = PricingPolicy.objects.get_or_create(
            status="ACTIVE",
            defaults={
                "name": "Standard Rate",
                "revision": 1,
                "currency": "INR",
            },
        )
        quote = Quote.objects.create(
            request=req,
            pricing_policy=policy,
            pricing_snapshot={},
            total_paise=47200,
            currency="INR",
            expires_at=timezone.now(),
            accepted_at=timezone.now(),
        )
        return ServiceJob.objects.create(
            request=req,
            accepted_quote=quote,
            state="COMPLETED",
            version=1,
        )

    def test_rating_first_write_creates_version_1(self):
        job = self._create_completed_job(self.customer1)
        res = repo.upsert_rating_for_job(self.customer1, job.id, rating=5, comment="Excellent")
        self.assertEqual(res["rating"], 5)
        self.assertEqual(res["version"], 1)

    def test_rating_subsequent_update_preconditions(self):
        job = self._create_completed_job(self.customer1)
        # Create initial rating
        repo.upsert_rating_for_job(self.customer1, job.id, rating=5, comment="Initial")

        # Subsequent update without If-Match (version=None) must be rejected
        with self.assertRaises(DomainError) as ctx:
            repo.upsert_rating_for_job(self.customer1, job.id, rating=4, comment="Updated", version=None)
        self.assertEqual(ctx.exception.code, "PRECONDITION_REQUIRED")

        # Subsequent update with stale version must be rejected
        with self.assertRaises(DomainError) as ctx:
            repo.upsert_rating_for_job(self.customer1, job.id, rating=4, comment="Updated", version=99)
        self.assertEqual(ctx.exception.code, "RESOURCE_VERSION_MISMATCH")

        # Identical repeated submission with version=1 returns current state without incrementing version
        res_same = repo.upsert_rating_for_job(self.customer1, job.id, rating=5, comment="Initial", version=1)
        self.assertEqual(res_same["rating"], 5)
        self.assertEqual(res_same["version"], 1)  # Version stays 1!

        # Modified submission with version=1 increments version to 2
        res_diff = repo.upsert_rating_for_job(self.customer1, job.id, rating=4, comment="Revised", version=1)
        self.assertEqual(res_diff["rating"], 4)
        self.assertEqual(res_diff["version"], 2)

    def test_rating_ownership_isolation(self):
        job = self._create_completed_job(self.customer1)
        # Customer 2 cannot rate Customer 1's job
        with self.assertRaises(DomainError) as ctx:
            repo.upsert_rating_for_job(self.customer2, job.id, rating=5)
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    # ========================================================================
    # 5. Notification Serialization & Delivery Preference Tests
    # ========================================================================

    def test_notification_serialization_matches_ninja_schema(self):
        notif = repo.create_notification(
            customer=self.customer1,
            title="Battery Ready",
            message="Your vehicle reached 80% charge.",
            notification_type="SERVICE",
            data={"session_id": "sess_123"},
        )
        formatted = repo.format_notification(notif)

        # Validate against strict NotificationData schema (must not raise ValidationError)
        validated = schemas.NotificationData.model_validate(formatted)
        self.assertEqual(validated.title, "Battery Ready")
        self.assertEqual(validated.body, "Your vehicle reached 80% charge.")
        self.assertEqual(validated.category, "SERVICE")
        self.assertFalse(validated.is_read)
        self.assertIsNone(validated.read_at)
        self.assertEqual(validated.data["session_id"], "sess_123")

    def test_notification_delivery_preferences_respected(self):
        # Default preferences: service_updates=True, promotions=False
        pref, _ = CustomerNotificationPreference.objects.get_or_create(customer=self.customer1)
        self.assertTrue(pref.service_updates)
        self.assertFalse(pref.promotions)

        # Promotional notification should be suppressed
        n_promo = repo.create_notification(self.customer1, "Discount", "Save 20%", "PROMOTION")
        self.assertIsNone(n_promo)

        # Service notification should be created
        n_service = repo.create_notification(self.customer1, "Update", "Technician arrived", "SERVICE")
        self.assertIsNotNone(n_service)

        # Update preference to enable promotions
        repo.update_notification_preferences(self.customer1, {"promotions": True, "service_updates": False})
        n_promo2 = repo.create_notification(self.customer1, "Discount 2", "Save 30%", "PROMOTION")
        self.assertIsNotNone(n_promo2)

        # Now service updates should be suppressed
        n_service2 = repo.create_notification(self.customer1, "Update 2", "Arrived", "SERVICE")
        self.assertIsNone(n_service2)

    def test_notification_mark_read_and_read_all(self):
        # Enable service updates
        repo.update_notification_preferences(self.customer1, {"service_updates": True})
        n1 = repo.create_notification(self.customer1, "N1", "Message 1", "SERVICE")
        n2 = repo.create_notification(self.customer1, "N2", "Message 2", "SERVICE")

        self.assertEqual(repo.get_unread_notification_count(self.customer1), 2)

        # Mark single read
        repo.mark_notification_read(self.customer1, n1.id)
        self.assertEqual(repo.get_unread_notification_count(self.customer1), 1)

        # Mark all read
        repo.mark_all_notifications_read(self.customer1)
        self.assertEqual(repo.get_unread_notification_count(self.customer1), 0)

    # ========================================================================
    # 6. List Queries Bounded Limit Tests
    # ========================================================================

    def test_list_queries_bounds_clamp_to_1_and_100(self):
        # Create 3 notifications
        repo.update_notification_preferences(self.customer1, {"service_updates": True})
        for i in range(3):
            repo.create_notification(self.customer1, f"Title {i}", f"Msg {i}", "SERVICE")

        # Negative or 0 limit clamps to 1
        res_zero = repo.list_notifications(self.customer1, limit=0)
        self.assertEqual(len(res_zero), 1)

        # Huge limit clamps to 100 max
        res_huge = repo.list_notifications(self.customer1, limit=5000)
        self.assertEqual(len(res_huge), 3)

    # ========================================================================
    # 7. Cross-Customer Isolation & Job / Meter Behavior Tests
    # ========================================================================

    def test_wallet_cross_customer_isolation(self):
        order1 = repo.create_payment_order(self.customer1, 25000, "INR", "order_iso_1", "key_1")
        pay_id = f"pay_{uuid4().hex[:12]}"
        sig = self._compute_sig(order1["provider_order_id"], pay_id)
        ev = {"id": pay_id, "order_id": order1["provider_order_id"], "status": "captured", "amount": 25000, "currency": "INR"}

        # Customer 2 cannot verify or credit Customer 1's order
        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(self.customer2, order1["provider_order_id"], pay_id, sig, self.secret, ev)
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

        # Customer 2's wallet ledger is completely independent
        w2_data, _ = repo.get_or_create_wallet(self.customer2)
        self.assertEqual(w2_data["balance_paise"], 0)
        self.assertEqual(len(repo.list_wallet_transactions(self.customer2)), 0)

    def test_job_cancellation_and_live_meter_observations(self):
        job = self._create_completed_job(self.customer1)
        job.state = "EN_ROUTE"
        job.save()

        from cow.models import Charger, HALFact
        charger = Charger.objects.create(
            asset_tag=f"CHG_{uuid4().hex[:6]}",
            manufacturer="ABB",
            model="Terra",
            active=True,
        )

        # Add charging session and meter observation
        session = ChargingSession.objects.create(
            job=job,
            charger=charger,
            connector_number=1,
            state="CHARGING",
            meter_start_wh=1000,
            last_meter_wh=5000,
            started_at=timezone.now(),
        )
        fact = HALFact.objects.create(
            event_id=uuid4(),
            kind="METER_VALUE",
            contract_version="v1",
            payload_digest="digest",
            charger=charger,
            body={},
            emitted_at=timezone.now(),
        )
        obs = MeterObservation.objects.create(
            session=session,
            fact=fact,
            energy_wh=5000,
            observed_at=timezone.now(),
        )

        live = repo.get_job_live(self.customer1, job.id)
        self.assertEqual(live["job_id"], str(job.id))
        self.assertEqual(live["state"], "EN_ROUTE")
        self.assertIsNone(live["eta_minutes"])  # No fabricated 15m ETA
        self.assertIsNotNone(live["charging_status"])
        self.assertEqual(live["charging_status"]["latest_meter"]["energy_wh"], 5000)

        # Test cancellation on cancellable state (e.g. DISPATCHING)
        job.state = "DISPATCHING"
        job.save()
        cancel_res = repo.cancel_job(self.customer1, job.id, version=job.version, reason="Customer request")
        self.assertEqual(cancel_res["state"], "CANCELLED")
        job.refresh_from_db()
        self.assertEqual(job.state, "CANCELLED")

    def test_service_layer_requires_idempotency_key(self):
        class DummyRequest:
            headers = {}
            content_type = "application/json"

        with self.assertRaises(DomainError) as ctx:
            service.get_idempotency_key(DummyRequest(), required=True)
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_KEY_REQUIRED")
        self.assertEqual(ctx.exception.status_code, 428)


class UserHTTPBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_schema_tables()
        cls.client = Client()

    def test_unauthenticated_wallet_requests_rejected(self):
        res = self.client.get("/api/v1/app/wallet")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"]["code"], "INVALID_CREDENTIALS")

        res_post = self.client.post("/api/v1/app/wallet/recharge/order", data=b"{}", content_type="application/json")
        self.assertEqual(res_post.status_code, 401)
        self.assertEqual(res_post.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_unsupported_media_type_rejected(self):
        res = self.client.post("/api/v1/app/auth/login", data=b"email=a&password=b", content_type="application/x-www-form-urlencoded")
        self.assertEqual(res.status_code, 415)
        self.assertEqual(res.json()["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")


if __name__ == "__main__":
    unittest.main()
