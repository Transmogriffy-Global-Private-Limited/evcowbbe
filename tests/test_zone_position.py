import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from cow.domain.zone_position import VerifiedPosition, radial_relation, ZonePositionError

AT = datetime(2026, 11, 4, 8, 30, tzinfo=timezone.utc)


def point(lat="0", lon="0", accuracy="0", observed_at=AT, ref="VERIFIED:TEST"):
    return VerifiedPosition(lat, lon, accuracy, observed_at, ref)


class VerifiedLocationTests(unittest.TestCase):
    def test_centre_inside(self):
        self.assertEqual(radial_relation("0", "0", 1000, point()), "INSIDE")

    def test_far_outside(self):
        self.assertEqual(radial_relation("0", "0", 1000, point(lon="1")), "OUTSIDE")

    def test_exact_centre_with_max_accuracy_inside_if_radius_sufficient(self):
        self.assertEqual(radial_relation(0, 0, 100, point(accuracy="100")), "INSIDE")

    def test_exact_centre_radius_under_uncertainty_unknown(self):
        self.assertEqual(radial_relation(0, 0, 99, point(accuracy="100")), "UNKNOWN")

    def test_inaccurate_sample_unknown(self):
        self.assertEqual(radial_relation(0, 0, 1000, point(accuracy="101")), "UNKNOWN")

    def test_east_boundary_straddling_unknown(self):
        self.assertEqual(radial_relation(0, 0, 1112, point(lon="0.01", accuracy="2")), "UNKNOWN")

    def test_east_boundary_fully_outside(self):
        self.assertEqual(radial_relation(0, 0, 1000, point(lon="0.01", accuracy="2")), "OUTSIDE")

    def test_east_boundary_fully_inside(self):
        self.assertEqual(radial_relation(0, 0, 1200, point(lon="0.01", accuracy="2")), "INSIDE")

    def test_antimeridian_near(self):
        self.assertEqual(radial_relation("0", "179.999", 1000, point(lon="-179.999")), "INSIDE")

    def test_southern_hemisphere(self):
        self.assertEqual(radial_relation(-33, 18, 1000, point(lat="-33.0001",lon="18")), "INSIDE")

    def test_decimal_string_normalization(self):
        p=point("1.1234567", "2.0", "0.50")
        self.assertEqual(p.latitude, Decimal("1.1234567"))
        self.assertEqual(p.accuracy_m, Decimal("0.50"))

    def test_float_rejected(self):
        with self.assertRaises(ZonePositionError): point(1.2)

    def test_boolean_rejected(self):
        with self.assertRaises(ZonePositionError): point(accuracy=True)

    def test_coordinate_out_of_range(self):
        for raw in ("90.0000001", "-90.0000001", "NaN", "Infinity"):
            with self.subTest(raw=raw), self.assertRaises(ZonePositionError): point(lat=raw)

    def test_longitude_out_of_range(self):
        for raw in ("180.00001", "-180.00001"):
            with self.subTest(raw=raw), self.assertRaises(ZonePositionError): point(lon=raw)

    def test_invalid_accuracy(self):
        for raw in ("-1", "100001", "Infinity"):
            with self.subTest(raw=raw), self.assertRaises(ZonePositionError): point(accuracy=raw)

    def test_naive_date_rejected(self):
        with self.assertRaises(ZonePositionError): point(observed_at=datetime(2026,11,4))

    def test_missing_evidence_rejected(self):
        with self.assertRaises(ZonePositionError): point(ref=" ")

    def test_invalid_zone_parameters(self):
        with self.assertRaises(ZonePositionError): radial_relation(0,0,0,point())
        with self.assertRaises(ZonePositionError): radial_relation("Infinity",0,1000,point())
        with self.assertRaises(ZonePositionError): radial_relation(0,200,1000,point())

    def test_not_a_verified_position(self):
        with self.assertRaises(ZonePositionError): radial_relation(0,0,1000,{"lat": 0})

    def test_accuracy_threshold_override(self):
        self.assertEqual(radial_relation(0,0,1000,point(accuracy="101"), max_accuracy_m=200), "INSIDE")
        self.assertEqual(radial_relation(0,0,1000,point(accuracy="101"), max_accuracy_m=100), "UNKNOWN")

    def test_time_kept_as_evidence(self):
        before=AT-timedelta(seconds=25)
        self.assertEqual(point(observed_at=before).observed_at,before)


if __name__ == '__main__': unittest.main()
