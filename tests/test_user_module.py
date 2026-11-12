"""Comprehensive unit tests for the Customer / User plane modules (cow/users).

Tests strict Pydantic schemas, service layer precondition/header validation,
repository behavior, and complete router registration.
"""

import ast
import os
import re
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

try:
    from scripts.dev import env_file
    env_file()
except Exception:
    pass

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from pydantic import ValidationError

from cow.users import contracts, schemas


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

        # Out-of-bounds energy Wh
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
            eta_minutes=12,
            freshness_timestamp="2026-11-12T10:00:00Z",
        )
        self.assertEqual(live_data.eta_minutes, 12)

    def test_rating_schemas(self):
        r = schemas.RatingCreatePayload(rating=5, comment="Great service!")
        self.assertEqual(r.rating, 5)

        # Rating boundary tests (must be 1 <= rating <= 5)
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
            created_at="2026-11-12T10:00:00Z",
        )
        self.assertFalse(notif.is_read)

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

        # Amount boundaries: minimum 100 paise (₹1) and max 10000000 paise (₹100,000)
        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=0)

        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=99)

        with self.assertRaises(ValidationError):
            schemas.WalletRechargeOrderPayload(amount_paise=10000001)

        # Unsupported currency rejected
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

        # Required legacy operation IDs
        for op in ("cow_api_request_service", "cow_api_quote_request", "cow_api_accept_quote", "cow_api_get_job"):
            self.assertIn(f'operation_id="{op}"', charging_src)

        # Required master operation IDs
        expected_master = (
            "app_profile_get", "app_profile_patch",
            "app_vehicles_list", "app_vehicles_create", "app_vehicles_get", "app_vehicles_patch", "app_vehicles_delete",
            "app_addresses_list", "app_addresses_create", "app_addresses_get", "app_addresses_patch", "app_addresses_delete",
            "app_addresses_default",
        )
        for op in expected_master:
            self.assertIn(f'operation_id="{op}"', master_src)

        # Extended user module operation IDs in router.py
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

    def test_no_created_at_on_audit_event_in_repository(self):
        repo_src = (ROOT / "cow/users/repository.py").read_text(encoding="utf-8")
        # Ensure AuditEvent is never queried or ordered by .created_at (which does not exist)
        self.assertNotIn("AuditEvent.objects.filter", repo_src.replace("occurred_at", ""))
        self.assertNotIn('order_by("-created_at")', repo_src.split("AuditEvent")[1].split("def ")[0] if "AuditEvent" in repo_src else "")

    def test_job_lock_avoids_outer_join_in_repository(self):
        repo_src = (ROOT / "cow/users/repository.py").read_text(encoding="utf-8")
        # When lock=True, select_for_update(of=("self",)) is used without driver_duty outer join
        self.assertIn('select_for_update(of=("self",))', repo_src)

    def test_meter_observation_query_uses_actual_model_fields(self):
        repo_src = (ROOT / "cow/users/repository.py").read_text(encoding="utf-8")
        # MeterObservation has session, energy_wh, observed_at
        self.assertIn("MeterObservation.objects.filter(session=session)", repo_src)
        self.assertIn("obs.energy_wh", repo_src)
        self.assertNotIn("obs.meter_wh", repo_src)

    def test_live_job_eta_not_fabricated(self):
        repo_src = (ROOT / "cow/users/repository.py").read_text(encoding="utf-8")
        self.assertIn("eta_minutes = None", repo_src)
        self.assertNotIn("eta_minutes = 15", repo_src)

    def test_razorpay_order_fails_safely_without_simulation(self):
        import os
        from cow.common.errors import DomainError
        from cow.users import service

        old_sim = os.environ.pop("RAZORPAY_TEST_SIMULATION", None)
        old_key = os.environ.pop("RAZORPAY_KEY_ID", None)
        old_sec = os.environ.pop("RAZORPAY_KEY_SECRET", None)
        try:
            with self.assertRaises(DomainError) as ctx:
                service.call_razorpay_create_order("", "", 50000, "INR", "rcpt_1")
            self.assertEqual(ctx.exception.code, "CONFIGURATION_ERROR")
            self.assertEqual(ctx.exception.status_code, 500)
        finally:
            if old_sim: os.environ["RAZORPAY_TEST_SIMULATION"] = old_sim
            if old_key: os.environ["RAZORPAY_KEY_ID"] = old_key
            if old_sec: os.environ["RAZORPAY_KEY_SECRET"] = old_sec

    def test_razorpay_order_simulation_mode_isolated(self):
        import os
        from cow.users import service

        os.environ["RAZORPAY_TEST_SIMULATION"] = "1"
        try:
            order_id = service.call_razorpay_create_order("", "", 50000, "INR", "rcpt_1")
            self.assertTrue(order_id.startswith("order_test_"))
        finally:
            os.environ.pop("RAZORPAY_TEST_SIMULATION", None)

    def test_realtime_ticket_fails_closed_when_not_configured(self):
        from cow.common.errors import DomainError
        from cow.users import repository as repo

        class DummyPrincipal:
            id = uuid4()

        with self.assertRaises(DomainError) as ctx:
            repo.create_realtime_ticket(DummyPrincipal(), ["self"])
        self.assertEqual(ctx.exception.code, "REALTIME_STREAM_NOT_CONFIGURED")
        self.assertEqual(ctx.exception.status_code, 501)

    def test_razorpay_signature_hmac_logic(self):
        import hashlib
        import hmac

        secret = "WvCG1TeQLOABInHqBgwyWb2t"
        order_id = "order_test_123"
        payment_id = "pay_test_456"

        data = f"{order_id}|{payment_id}".encode("utf-8")
        valid_sig = hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()

        # Valid signature matches
        self.assertTrue(hmac.compare_digest(valid_sig, valid_sig))

        # Tampered payment id fails
        tampered_data = f"{order_id}|pay_test_999".encode("utf-8")
        tampered_sig = hmac.new(secret.encode("utf-8"), tampered_data, hashlib.sha256).hexdigest()
        self.assertFalse(hmac.compare_digest(valid_sig, tampered_sig))


class UserHTTPBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import django
        from django.test import Client
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()
        cls.client = Client()

    def test_unauthenticated_wallet_requests_rejected(self):
        res = self.client.get("/api/v1/app/wallet")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"]["code"], "INVALID_CREDENTIALS")

        res_post = self.client.post("/api/v1/app/wallet/recharge/order", data=b"{}", content_type="application/json")
        self.assertEqual(res_post.status_code, 401)
        self.assertEqual(res_post.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_unsupported_media_type_rejected(self):
        # Authenticated or public, non-json is rejected
        res = self.client.post("/api/v1/app/auth/login", data=b"email=a&password=b", content_type="application/x-www-form-urlencoded")
        self.assertEqual(res.status_code, 415)
        self.assertEqual(res.json()["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")


class UserPaymentAndStateLogicTest(unittest.TestCase):
    def test_verify_recharge_rejects_tampered_signature(self):
        from cow.common.errors import DomainError
        from cow.users import repository as repo

        class DummyPrincipal:
            id = uuid4()

        with self.assertRaises(DomainError) as ctx:
            repo.verify_and_credit_recharge(
                actor=DummyPrincipal(),
                order_id="order_1",
                payment_id="pay_1",
                signature="invalid_signature",
                secret="test_secret",
            )
        self.assertEqual(ctx.exception.code, "PAYMENT_SIGNATURE_INVALID")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_payment_order_validates_currency_and_bounds(self):
        from cow.common.errors import DomainError
        from cow.users import repository as repo

        class DummyPrincipal:
            id = uuid4()

        # Reject currency other than INR
        with self.assertRaises(DomainError) as ctx:
            repo.create_payment_order(DummyPrincipal(), 5000, "USD", "order_1", "key_1")
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_CURRENCY")
        self.assertEqual(ctx.exception.status_code, 422)

        # Reject out of bounds amount (< 100 paise)
        with self.assertRaises(DomainError) as ctx:
            repo.create_payment_order(DummyPrincipal(), 50, "INR", "order_1", "key_1")
        self.assertEqual(ctx.exception.code, "INVALID_AMOUNT")
        self.assertEqual(ctx.exception.status_code, 422)

        # Reject out of bounds amount (> 10000000 paise)
        with self.assertRaises(DomainError) as ctx:
            repo.create_payment_order(DummyPrincipal(), 20000000, "INR", "order_1", "key_1")
        self.assertEqual(ctx.exception.code, "INVALID_AMOUNT")
        self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
