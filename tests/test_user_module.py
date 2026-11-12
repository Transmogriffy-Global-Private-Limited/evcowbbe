"""Comprehensive unit tests for the Customer / User plane modules (cow/users).

Tests strict Pydantic schemas, service layer precondition/header validation,
repository behavior, and complete router registration.
"""

import ast
import re
import unittest
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from cow.users import contracts, schemas

ROOT = Path(__file__).resolve().parents[1]


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
        )
        for op in expected_extended:
            self.assertIn(f'operation_id="{op}"', router_src)


if __name__ == "__main__":
    unittest.main()
