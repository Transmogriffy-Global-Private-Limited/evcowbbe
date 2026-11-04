"""Pure contract tests: zero Django, network, provider, wallet or DB side effects."""
from __future__ import annotations

import hmac
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

from cow.domain.service_commerce import (
    BalanceEffect, BoundaryMode, CancelActor, CancelPhase, CancellationPolicy,
    ChargingMode, DecisionError, DistanceSlab, GeoEvidence, MinimumTreatment,
    OriginPolicy, Point, QrLocationBasis, QrStartPolicy, RefundRule,
    DriverPenaltyPolicy, check_quoted_driver, assess_driver_penalty,
    RouteEvidence, ServiceZone, Tariff, authorize_qr_start, cancel_settlement,
    evaluate_zone, quote_mobile as _quote_mobile, quote_mobile_for_driver,
    select_driver_zone_assignment, select_published_zone_tariff,
    DriverZoneAssignment, TariffStatus, success_settlement, verify_razorpay_webhook,
)

NOW = datetime(2026, 11, 4, 6, 15, tzinfo=timezone.utc)
ORIGIN = Point(Decimal('22.57260'), Decimal('88.36390'))
NEAR = Point(Decimal('22.57400'), Decimal('88.36390'))
FAR = Point(Decimal('23.00000'), Decimal('88.36390'))


def fixture_zone(mode=BoundaryMode.STRICT):
    return ServiceZone('kolkata-north', 3, ORIGIN, 3000, mode)


def fixture_tariff(outside=True, origin=OriginPolicy.DRIVER_CURRENT,
                   minimum=MinimumTreatment.SPEND_FLOOR):
    return Tariff('mobile-rate', 9, NOW - timedelta(minutes=1),
        NOW + timedelta(minutes=20), 'kolkata-north', 3,
        (DistanceSlab(0, 10000, 10000, 20000), DistanceSlab(10000, None, 19000, 25000)),
        (DistanceSlab(0, 10000, 35000, 20000), DistanceSlab(10000, None, 50000, 30000)) if outside else (),
        minimum, origin, status=TariffStatus.PUBLISHED)


def fixture_route(distance=9999, dest=NEAR, origin_policy=OriginPolicy.DRIVER_CURRENT):
    return RouteEvidence('maps-routing', 'rt-9', ORIGIN, dest, distance,
        420, NOW - timedelta(seconds=3), NOW + timedelta(minutes=2),
        origin_policy, 'driver-1' if origin_policy == OriginPolicy.DRIVER_CURRENT else None)


def fixture_assignment(*, driver='driver-1', zone='kolkata-north',
                       assignment_id='assignment-1', primary=True, start=None, end=None):
    return DriverZoneAssignment(assignment_id, driver, zone,
        start or NOW - timedelta(minutes=5), end, primary)


def quote_mobile(*args, **kwargs):
    """Legacy fixture cases share an explicit, valid zone assignment."""
    kwargs.setdefault('assignment', fixture_assignment())
    return _quote_mobile(*args, **kwargs)


def quote(distance=9999):
    return quote_mobile(fixture_zone(), fixture_tariff(), fixture_route(distance), NEAR, NOW)


def geo(point=NEAR, *, trusted=True, age=0, accuracy='1'):
    return GeoEvidence(point, NOW - timedelta(seconds=age), Decimal(accuracy), 'trusted-adapter', trusted)


class ServiceCommerceTest(unittest.TestCase):
    def raises(self, code, fn, *args, **kwargs):
        with self.assertRaises(DecisionError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def test_zone_default_strict_inside(self):
        x = evaluate_zone(fixture_zone(), NEAR)
        self.assertTrue(x.permitted)
        self.assertTrue(x.within_zone)
        self.assertFalse(x.advisory_alert)

    def test_zone_strict_outside_denies(self):
        x = evaluate_zone(fixture_zone(), FAR)
        self.assertFalse(x.permitted)
        self.assertFalse(x.advisory_alert)
        self.raises('OUTSIDE_STRICT_ZONE', quote_mobile, fixture_zone(),
                    fixture_tariff(), fixture_route(dest=FAR), FAR, NOW)

    def test_advisory_zone_allows_and_warns_outside(self):
        x = evaluate_zone(fixture_zone(BoundaryMode.ADVISORY), FAR)
        self.assertTrue(x.permitted)
        self.assertTrue(x.advisory_alert)
        self.assertEqual(x.pricing_side, 'OUTSIDE')
        q = quote_mobile(fixture_zone(BoundaryMode.ADVISORY), fixture_tariff(),
                         fixture_route(dest=FAR), FAR, NOW)
        self.assertEqual(q.upfront_minor, 55000)

    def test_advisory_without_outside_tariff_fails_closed(self):
        self.raises('OUTSIDE_TARIFF_NOT_CONFIGURED', quote_mobile,
            fixture_zone(BoundaryMode.ADVISORY), fixture_tariff(outside=False),
            fixture_route(dest=FAR), FAR, NOW)

    def test_zone_centre_is_not_the_tariff_origin(self):
        self.assertEqual(fixture_tariff().route_origin, OriginPolicy.DRIVER_CURRENT)
        self.raises('ROUTE_ORIGIN_POLICY_MISMATCH', quote_mobile,
            fixture_zone(), fixture_tariff(),
            fixture_route(origin_policy=OriginPolicy.ZONE_CENTRE), NEAR, NOW)

    def test_centre_is_explicit_opt_in(self):
        q = quote_mobile(fixture_zone(), fixture_tariff(origin=OriginPolicy.ZONE_CENTRE),
                         fixture_route(origin_policy=OriginPolicy.ZONE_CENTRE), NEAR, NOW)
        self.assertEqual(q.quoted_driver_id, 'driver-1')
        self.assertEqual(q.route_origin, OriginPolicy.ZONE_CENTRE)

    def test_slab_exact_boundary_uses_next_slab(self):
        self.assertEqual(quote(9999).upfront_minor, 30000)
        self.assertEqual(quote(10000).upfront_minor, 44000)

    def test_slab_missing_root_disallowed(self):
        self.raises('NONCONTIGUOUS_TARIFF_SLABS', Tariff,
            'broken', 1, NOW, None, 'z', 1,
            (DistanceSlab(5, None, 1, 2),))

    def test_slab_gap_disallowed(self):
        self.raises('NONCONTIGUOUS_TARIFF_SLABS', Tariff,
            'broken', 1, NOW, None, 'z', 1,
            (DistanceSlab(0, 5, 1, 2), DistanceSlab(6, None, 3, 4)))

    def test_slab_overlap_disallowed(self):
        self.raises('NONCONTIGUOUS_TARIFF_SLABS', Tariff,
            'broken', 1, NOW, None, 'z', 1,
            (DistanceSlab(0, 5, 1, 2), DistanceSlab(4, None, 3, 4)))

    def test_slab_open_not_last_disallowed(self):
        self.raises('OPEN_SLAB_NOT_LAST', Tariff,
            'broken', 1, NOW, None, 'z', 1,
            (DistanceSlab(0, None, 1, 2), DistanceSlab(1, None, 3, 4)))

    def test_route_and_zone_version_frozen(self):
        q = quote()
        self.assertEqual((q.zone_version, q.policy_revision, q.route_ref), (3, 9, 'rt-9'))
        self.assertEqual(q.upfront_minor, q.transport_minor + q.minimum_minor)

    def test_zone_version_drift_blocks(self):
        self.raises('TARIFF_ZONE_DRIFT', quote_mobile,
            replace(fixture_zone(), version=4), fixture_tariff(), fixture_route(), NEAR, NOW)

    def test_route_destination_change_blocks(self):
        self.raises('ROUTE_DESTINATION_CHANGED', quote_mobile,
            fixture_zone(), fixture_tariff(), fixture_route(dest=ORIGIN), NEAR, NOW)

    def test_route_expiration_blocks(self):
        self.raises('STALE_OR_FUTURE_ROUTE', quote_mobile,
            fixture_zone(), fixture_tariff(), fixture_route(), NEAR, NOW+timedelta(minutes=3))

    def test_route_future_observation_blocks(self):
        r = fixture_route()
        self.raises('STALE_OR_FUTURE_ROUTE', quote_mobile,
            fixture_zone(), fixture_tariff(), replace(r,observed_at=NOW+timedelta(seconds=1)), NEAR, NOW)

    def test_haversine_cannot_be_billed_as_road_distance(self):
        self.raises('ROAD_ROUTE_REQUIRED', quote_mobile,
            fixture_zone(), fixture_tariff(), replace(fixture_route(), provider='haversine'), NEAR, NOW)

    def test_driver_location_required_for_default_basis(self):
        self.raises('DRIVER_LOCATION_REQUIRED', RouteEvidence,
            'route', 'r', ORIGIN, NEAR, 5, None, NOW, NOW+timedelta(seconds=30),
            OriginPolicy.DRIVER_CURRENT, None)

    def test_tariff_effective_expiry_and_quote_expiry(self):
        self.raises('TARIFF_NOT_EFFECTIVE', quote_mobile,
            fixture_zone(), replace(fixture_tariff(), valid_from=NOW+timedelta(seconds=1)),
            fixture_route(), NEAR, NOW)
        q = quote()
        self.assertEqual(q.quote_expires_at, NOW+timedelta(seconds=120))

    def test_reject_boolean_money_and_bad_negative_values(self):
        self.raises('INVALID_MINOR_AMOUNT', DistanceSlab, 0, None, True, 0)
        self.raises('INVALID_MINOR_AMOUNT', DistanceSlab, 0, None, -1, 0)
        self.raises('INVALID_MINOR_AMOUNT', DistanceSlab, 0, None, 2**63, 0)

    def test_success_below_minimum_single_noop(self):
        d = success_settlement(quote(), verified_charging_minor=15000,
                               posted_upfront_minor=30000, available_minor=0)
        self.assertEqual((d.due_minor, d.effect, d.amount_minor), (30000, BalanceEffect.NONE, 0))
        self.assertFalse(d.requires_topup)

    def test_success_above_minimum_one_increment(self):
        d = success_settlement(quote(), verified_charging_minor=35000,
                               posted_upfront_minor=30000, available_minor=50000)
        self.assertEqual((d.due_minor, d.effect, d.amount_minor), (45000, BalanceEffect.DEBIT, 15000))
        self.assertFalse(d.requires_topup)

    def test_success_above_balance_signals_precondition(self):
        d = success_settlement(quote(), verified_charging_minor=35000,
                               posted_upfront_minor=30000, available_minor=14999)
        self.assertTrue(d.requires_topup)
        self.assertEqual(d.amount_minor, 15000)

    def test_refundable_advance_can_refund_unused_part(self):
        q = quote_mobile(fixture_zone(), fixture_tariff(minimum=MinimumTreatment.REFUNDABLE_ADVANCE),
                         fixture_route(), NEAR, NOW)
        d = success_settlement(q, verified_charging_minor=11000,
                               posted_upfront_minor=30000, available_minor=0)
        self.assertEqual((d.effect,d.amount_minor,d.due_minor), (BalanceEffect.CREDIT,9000,21000))

    def test_upfront_mismatch_rejected(self):
        self.raises('UPFRONT_LEDGER_MISMATCH', success_settlement, quote(),
            verified_charging_minor=100, posted_upfront_minor=29999, available_minor=10000)

    def test_cancellation_before_acceptance_full_default(self):
        rule = CancellationPolicy('cancel-9', 1, CancelActor.CUSTOMER,
                                  CancelPhase.BEFORE_ACCEPTANCE, RefundRule.FULL)
        d = cancel_settlement(quote(), rule, actor=CancelActor.CUSTOMER,
            phase=CancelPhase.BEFORE_ACCEPTANCE, posted_upfront_minor=30000)
        self.assertEqual((d.wallet_refund_minor,d.retained_minor), (30000,0))

    def test_driver_cancellation_can_be_full_refund(self):
        p = CancellationPolicy('driver', 2, CancelActor.DRIVER,
                               CancelPhase.EN_ROUTE, RefundRule.FULL)
        d = cancel_settlement(quote(),p,actor=CancelActor.DRIVER,
            phase=CancelPhase.EN_ROUTE,posted_upfront_minor=30000)
        self.assertEqual(d.wallet_refund_minor, 30000)

    def test_customer_cancellation_travel_prorated_and_capped(self):
        p = CancellationPolicy('customer', 2, CancelActor.CUSTOMER,
                               CancelPhase.EN_ROUTE, RefundRule.TRAVEL_PROPORTIONAL,
                               Decimal('5000'))
        self.raises('TRAVEL_EVIDENCE_REQUIRED', cancel_settlement, quote(), p,
            actor=CancelActor.CUSTOMER, phase=CancelPhase.EN_ROUTE,
            posted_upfront_minor=30000, travelled_m=3000, verified_travel_evidence=False)
        d = cancel_settlement(quote(),p,actor=CancelActor.CUSTOMER,
            phase=CancelPhase.EN_ROUTE,posted_upfront_minor=30000,
            travelled_m=3000, verified_travel_evidence=True)
        self.assertEqual((d.retained_minor,d.wallet_refund_minor), (10000,20000))

    def test_none_cancellation_refund_configurable(self):
        p = CancellationPolicy('customer', 3, CancelActor.CUSTOMER,
                               CancelPhase.ARRIVED, RefundRule.NONE)
        d = cancel_settlement(quote(),p,actor=CancelActor.CUSTOMER,
            phase=CancelPhase.ARRIVED,posted_upfront_minor=30000)
        self.assertEqual((d.retained_minor,d.wallet_refund_minor), (30000,0))

    def test_cancellation_charge_started_needs_stop_reconciliation(self):
        p = CancellationPolicy('test', 2, CancelActor.CUSTOMER,
                               CancelPhase.CHARGING_STARTED, RefundRule.FULL)
        self.raises('ACTIVE_CHARGING_REQUIRES_STOP_AND_RECONCILIATION',
            cancel_settlement,quote(),p,actor=CancelActor.CUSTOMER,
            phase=CancelPhase.CHARGING_STARTED,posted_upfront_minor=30000)

    def test_cancel_policy_actor_mismatch_blocks(self):
        p = CancellationPolicy('test', 1, CancelActor.CUSTOMER,
                               CancelPhase.EN_ROUTE, RefundRule.FULL)
        self.raises('CANCELLATION_POLICY_MISMATCH', cancel_settlement, quote(), p,
            actor=CancelActor.DRIVER, phase=CancelPhase.EN_ROUTE,posted_upfront_minor=30000)

    def test_qr_arrival_with_two_trusted_locations(self):
        authorize_qr_start(QrStartPolicy(200), now=NOW, target=NEAR,
            customer_observation=geo(), charger_observation=geo(),
            server_challenge_verified=True, charger_connector_verified=True)

    def test_qr_untrusted_geolocation_denied(self):
        self.raises('GEO_EVIDENCE_UNTRUSTED', authorize_qr_start,
            QrStartPolicy(200), now=NOW, target=NEAR,
            customer_observation=geo(trusted=False), charger_observation=geo(),
            server_challenge_verified=True, charger_connector_verified=True)

    def test_qr_stale_evidence_denied(self):
        self.raises('GEO_EVIDENCE_STALE_OR_FUTURE', authorize_qr_start,
            QrStartPolicy(200), now=NOW, target=NEAR,
            customer_observation=geo(age=61), charger_observation=geo(),
            server_challenge_verified=True, charger_connector_verified=True)

    def test_qr_inaccurate_evidence_denied(self):
        self.raises('GEO_EVIDENCE_TOO_INACCURATE', authorize_qr_start,
            QrStartPolicy(200), now=NOW, target=NEAR,
            customer_observation=geo(accuracy='50'), charger_observation=geo(),
            server_challenge_verified=True, charger_connector_verified=True)

    def test_qr_out_of_radius_denied(self):
        self.raises('QR_OUTSIDE_ALLOWED_LOCATION', authorize_qr_start,
            QrStartPolicy(200), now=NOW, target=NEAR,
            customer_observation=geo(), charger_observation=geo(FAR),
            server_challenge_verified=True, charger_connector_verified=True)

    def test_qr_requires_server_challenge_and_connector(self):
        self.raises('QR_CLAIM_UNVERIFIED', authorize_qr_start,
            QrStartPolicy(200), now=NOW, target=NEAR,
            customer_observation=geo(), charger_observation=geo(),
            server_challenge_verified=False, charger_connector_verified=True)

    def test_qr_current_location_basis_requires_consent(self):
        self.raises('CURRENT_LOCATION_CONSENT_REQUIRED', authorize_qr_start,
            QrStartPolicy(200, QrLocationBasis.CURRENT_CUSTOMER), now=NOW, target=NEAR,
            customer_observation=geo(), charger_observation=geo(),
            server_challenge_verified=True, charger_connector_verified=True)

    def test_qr_current_location_with_consent(self):
        authorize_qr_start(QrStartPolicy(200, QrLocationBasis.CURRENT_CUSTOMER),
            now=NOW, target=NEAR, customer_observation=geo(),
            charger_observation=geo(), current_customer_consent=True,
            server_challenge_verified=True, charger_connector_verified=True)

    def test_candidate_change_requires_requote_without_silent_charge(self):
        result = check_quoted_driver(quote(), accepting_driver_id='different-driver', now=NOW, active_assignment=fixture_assignment())
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, 'DRIVER_CHANGED_REQUOTE_REQUIRED')
        self.assertTrue(check_quoted_driver(quote(), accepting_driver_id='driver-1', now=NOW, active_assignment=fixture_assignment()).accepted)

    def test_quote_expiry_blocks_driver_acceptance(self):
        result = check_quoted_driver(quote(), accepting_driver_id='driver-1',
                                      now=NOW+timedelta(minutes=3), active_assignment=fixture_assignment())
        self.assertEqual(result.reason, 'QUOTE_EXPIRED')

    def test_driver_penalty_disabled_by_default(self):
        policy = DriverPenaltyPolicy('policy', 1)
        self.assertEqual(assess_driver_penalty(policy, cancellation_by_driver=True,
            liability_confirmed=False, travel_m=None), 0)

    def test_driver_penalty_requires_separate_review(self):
        policy = DriverPenaltyPolicy('policy', 1, flat_minor=1000,
                                    travel_rate_minor_per_km=Decimal('200'),
                                    max_penalty_minor=1500, active=True)
        self.raises('DRIVER_PENALTY_REVIEW_REQUIRED', assess_driver_penalty, policy,
            cancellation_by_driver=True, liability_confirmed=False, travel_m=4000)
        self.assertEqual(assess_driver_penalty(policy, cancellation_by_driver=True,
            liability_confirmed=True, travel_m=4000), 1500)
        self.assertEqual(assess_driver_penalty(policy, cancellation_by_driver=False,
            liability_confirmed=False, travel_m=None), 0)

    def test_tariff_unpublished_cannot_be_quoted(self):
        self.raises('TARIFF_NOT_PUBLISHED', quote_mobile, fixture_zone(),
            replace(fixture_tariff(), status=TariffStatus.DRAFT),
            fixture_route(), NEAR, NOW)
        self.raises('TARIFF_NOT_PUBLISHED', quote_mobile, fixture_zone(),
            replace(fixture_tariff(), status=TariffStatus.WITHDRAWN),
            fixture_route(), NEAR, NOW)

    def test_mobile_quote_requires_driver_zone_assignment(self):
        self.raises('DRIVER_ZONE_ASSIGNMENT_INACTIVE', _quote_mobile,
            fixture_zone(), fixture_tariff(), fixture_route(), NEAR, NOW,
            assignment=fixture_assignment(start=NOW+timedelta(seconds=1)))
        with self.assertRaises(TypeError):
            _quote_mobile(fixture_zone(), fixture_tariff(), fixture_route(), NEAR, NOW)

    def test_other_zone_tariff_cannot_price_selected_driver(self):
        wrong = replace(fixture_tariff(), zone_id='different-zone')
        self.raises('DRIVER_ZONE_TARIFF_MISMATCH', quote_mobile, fixture_zone(),
            fixture_tariff(), fixture_route(), NEAR, NOW,
            assignment=fixture_assignment(zone='different-zone'))
        self.raises('TARIFF_ZONE_DRIFT', quote_mobile, fixture_zone(),
            wrong, fixture_route(), NEAR, NOW)
        self.raises('ASSIGNED_ZONE_TARIFF_NOT_PUBLISHED', quote_mobile_for_driver,
            driver_id='driver-1', assignments=(fixture_assignment(),),
            zones=(fixture_zone(),), tariffs=(wrong,),
            route=fixture_route(), customer_location=NEAR, now=NOW)

    def test_overlapping_zones_never_change_driver_tariff(self):
        # Customer is outside A but inside B: same Driver still uses A OUTSIDE.
        zone_a = fixture_zone(BoundaryMode.ADVISORY)
        zone_b = ServiceZone('zone-b', 7, FAR, 3000)
        tariff_b = replace(fixture_tariff(), zone_id='zone-b', zone_version=7,
                           policy_id='tariff-b', inside=(DistanceSlab(0, None, 1, 1),))
        result = quote_mobile_for_driver(
            driver_id='driver-1', assignments=(fixture_assignment(),),
            zones=(zone_a,zone_b), tariffs=(tariff_b,fixture_tariff()),
            route=fixture_route(dest=FAR), customer_location=FAR, now=NOW)
        self.assertEqual((result.zone_id, result.within_zone), ('kolkata-north', False))
        self.assertEqual((result.transport_minor, result.minimum_minor), (35000, 20000))
        self.assertEqual(result.driver_assignment_id, 'assignment-1')

    def test_strict_driver_zone_rejects_even_if_other_zone_covers_customer(self):
        zone_a = fixture_zone(BoundaryMode.STRICT)
        zone_b = ServiceZone('zone-b', 7, FAR, 3000)
        tariff_b = replace(fixture_tariff(), zone_id='zone-b', zone_version=7,
                           policy_id='tariff-b')
        self.raises('OUTSIDE_STRICT_ZONE', quote_mobile_for_driver,
            driver_id='driver-1', assignments=(fixture_assignment(),),
            zones=(zone_a,zone_b), tariffs=(fixture_tariff(),tariff_b),
            route=fixture_route(dest=FAR), customer_location=FAR, now=NOW)

    def test_multiple_driver_zones_choose_unique_primary_not_cheapest(self):
        a = fixture_assignment()
        b = fixture_assignment(zone='zone-b', assignment_id='assignment-b', primary=False)
        picked = select_driver_zone_assignment('driver-1',(b,a),NOW)
        self.assertEqual(picked.assignment_id, 'assignment-1')
        self.assertEqual(select_driver_zone_assignment('driver-1',(a,b),NOW,
            selected_assignment_id='assignment-b').zone_id, 'zone-b')
        self.raises('DRIVER_ZONE_SELECTION_INVALID',select_driver_zone_assignment,
            'driver-1',(a,b),NOW,selected_assignment_id='none')

    def test_multi_assignment_without_primary_fails_closed(self):
        a=fixture_assignment(primary=False)
        b=fixture_assignment(zone='zone-b',assignment_id='assignment-b',primary=False)
        self.raises('DRIVER_ZONE_SELECTION_REQUIRED',select_driver_zone_assignment,
            'driver-1',(a,b),NOW)
        self.raises('MULTIPLE_PRIMARY_DRIVER_ZONES',select_driver_zone_assignment,
            'driver-1',(replace(a,primary=True),replace(b,primary=True)),NOW)
        self.raises('DUPLICATE_ACTIVE_DRIVER_ZONE',select_driver_zone_assignment,
            'driver-1',(a,replace(b,zone_id=a.zone_id)),NOW)

    def test_driver_zone_assignment_expires_and_quote_ttl_bounded(self):
        a=fixture_assignment(end=NOW+timedelta(seconds=35))
        q=quote_mobile_for_driver(driver_id='driver-1',assignments=(a,),
            zones=(fixture_zone(),),tariffs=(fixture_tariff(),),
            route=fixture_route(),customer_location=NEAR,now=NOW)
        self.assertEqual(q.quote_expires_at,NOW+timedelta(seconds=35))
        check=check_quoted_driver(q,accepting_driver_id='driver-1',
            now=NOW+timedelta(seconds=36),active_assignment=a)
        self.assertFalse(check.accepted)
        self.assertEqual(check.reason,'QUOTE_EXPIRED')

    def test_driver_assignment_revocation_or_zone_switch_blocks_accept(self):
        q=quote()
        for a in (fixture_assignment(end=NOW),
                  fixture_assignment(zone='zone-b'),
                  fixture_assignment(assignment_id='assignment-2'),
                  fixture_assignment(driver='driver-2')):
            check=check_quoted_driver(q,accepting_driver_id='driver-1',
                now=NOW,active_assignment=a)
            self.assertFalse(check.accepted)
            self.assertEqual(check.reason,'DRIVER_ZONE_ASSIGNMENT_CHANGED')

    def test_route_driver_must_equal_assigned_driver(self):
        self.raises('ROUTE_DRIVER_ASSIGNMENT_MISMATCH',quote_mobile,
            fixture_zone(),fixture_tariff(),fixture_route(),NEAR,NOW,
            assignment=fixture_assignment(driver='driver-2'))

    def test_centre_origin_still_binds_quote_to_assigned_driver(self):
        q=quote_mobile_for_driver(driver_id='driver-1',
            assignments=(fixture_assignment(),),zones=(fixture_zone(),),
            tariffs=(fixture_tariff(origin=OriginPolicy.ZONE_CENTRE),),
            route=fixture_route(origin_policy=OriginPolicy.ZONE_CENTRE),
            customer_location=NEAR,now=NOW)
        self.assertEqual(q.quoted_driver_id,'driver-1')
        self.assertEqual(q.driver_assignment_id,'assignment-1')
        self.assertFalse(check_quoted_driver(q,accepting_driver_id='driver-2',
            now=NOW,active_assignment=fixture_assignment()).accepted)

    def test_zone_tariff_conflict_fails_closed_no_auto_cheapest(self):
        a=fixture_tariff()
        b=replace(a,policy_id='another-competitive-tariff',revision=10)
        self.raises('AMBIGUOUS_PUBLISHED_ZONE_TARIFF',select_published_zone_tariff,
            fixture_zone(),(a,b),NOW)
        self.assertIs(select_published_zone_tariff(fixture_zone(),(a,),NOW),a)
        self.raises('ASSIGNED_ZONE_TARIFF_NOT_PUBLISHED',select_published_zone_tariff,
            fixture_zone(),(replace(a,status=TariffStatus.DRAFT),),NOW)

    def test_cross_driver_request_never_picks_other_zone_without_assignment(self):
        self.raises('DRIVER_NOT_ASSIGNED_TO_ACTIVE_ZONE',quote_mobile_for_driver,
            driver_id='driver-unassigned',assignments=(fixture_assignment(),),
            zones=(fixture_zone(),),tariffs=(fixture_tariff(),),
            route=fixture_route(),customer_location=NEAR,now=NOW)

    def test_driver_can_be_physically_outside_assigned_zone(self):
        # Assignment selects the tariff, not a hard fence on the moving Driver.
        moving_route = replace(fixture_route(), origin=FAR, route_ref='from-far')
        result = quote_mobile_for_driver(
            driver_id='driver-1', assignments=(fixture_assignment(),),
            zones=(fixture_zone(),), tariffs=(fixture_tariff(),),
            route=moving_route, customer_location=NEAR, now=NOW)
        self.assertTrue(result.within_zone)
        self.assertEqual(result.zone_id, 'kolkata-north')
        self.assertEqual(result.distance_m, moving_route.distance_m)

    def test_not_yet_issued_quote_is_not_acceptable(self):
        result = check_quoted_driver(quote(), accepting_driver_id='driver-1',
            now=NOW-timedelta(seconds=1), active_assignment=fixture_assignment())
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, 'QUOTE_NOT_YET_ACTIVE')

    def test_assignment_exact_end_exclusive(self):
        assignment = fixture_assignment(end=NOW+timedelta(seconds=10))
        self.assertTrue(assignment.active_at(NOW+timedelta(seconds=9)))
        self.assertFalse(assignment.active_at(NOW+timedelta(seconds=10)))

    def test_webhook_uses_raw_request_bytes_and_hmac(self):
        body = b'{"event":"payment.captured","x":1}'
        secret = b'test-webhook-secret'
        sig = hmac.new(secret, body, sha256).hexdigest()
        self.assertTrue(verify_razorpay_webhook(body,sig,secret))
        self.assertFalse(verify_razorpay_webhook(body+b' ',sig,secret))
        self.assertFalse(verify_razorpay_webhook(body,sig,b'different'))
        self.assertFalse(verify_razorpay_webhook(body,'bad',secret))
        self.assertFalse(verify_razorpay_webhook(body,sig,b''))


if __name__ == '__main__':
    unittest.main()
