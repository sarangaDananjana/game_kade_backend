from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from orders.test_fixtures import OrderFixtures
from .models import JobOffer, RiderInfo, RiderTopUp, RiderWalletEntry, RiderRoadRoute, Session
from .services import create_session, accept_offer, RiderActionError
from .wallet import acceptance_fee, review_topup
from .road_routes import road_route, _cached_segment, _google_directions


class RiderWalletTests(OrderFixtures):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.rider)
        self.order = self.make_order(rider=None)
        self.session = create_session(self.vendor.pk, [self.order.pk], rider_cut=Decimal('220.00'),
            fuel_surge_addition=Decimal('20.00'), dispatch_enabled=False, offer_to_rider=self.rider.pk)
        self.offer = self.session.job_offers.get(status='pending')

    def credit(self, amount):
        topup = RiderTopUp.objects.create(rider=self.rider.rider_info, amount=Decimal(amount))
        return review_topup(topup.pk, self.owner, True)

    def test_cash_credit_and_repeated_approval_only_credit_once(self):
        topup = self.credit('100.00')
        review_topup(topup.pk, self.owner, True)
        self.assertEqual(RiderInfo.objects.get(user=self.rider).topup_balance, Decimal('100.00'))
        self.assertEqual(RiderWalletEntry.objects.count(), 1)
        self.assertEqual(topup.reviewed_by, self.owner)

    def test_staff_can_add_collected_money_in_admin(self):
        from django.test import Client
        from django.urls import reverse
        self.owner.is_staff = True; self.owner.is_superuser = True
        self.owner.save(update_fields=['is_staff', 'is_superuser'])
        client = Client(); client.force_login(self.owner)
        response = client.post(reverse('admin:riders_ridertopup_add'), {
            'rider': self.rider.rider_info.pk, 'amount': '100.00',
            'payment_reference': 'cash-receipt-123', 'staff_note': 'Collected cash', '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RiderInfo.objects.get(user=self.rider).topup_balance, Decimal('100.00'))
        self.assertEqual(RiderTopUp.objects.get().status, 'approved')
        self.assertEqual(RiderWalletEntry.objects.count(), 1)

    def test_insufficient_funds_leave_offer_orders_and_balance_unchanged(self):
        self.credit('47.99')
        response = self.client.post(f'/api/riders/jobs/{self.offer.pk}/accept/', {})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'insufficient_topup_balance')
        self.offer.refresh_from_db(); self.session.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(self.offer.status, 'pending')
        self.assertEqual(self.session.status, 'planned')
        self.assertIsNone(self.order.rider_id)
        self.assertEqual(RiderWalletEntry.objects.filter(kind='job_fee').count(), 0)
        self.assertEqual(RiderInfo.objects.get(user=self.rider).topup_balance, Decimal('47.99'))

    def test_exact_balance_covers_fee_and_accept_retry_is_idempotent(self):
        self.credit('48.00')
        url = f'/api/riders/jobs/{self.offer.pk}/accept/'
        first, second = self.client.post(url, {}), self.client.post(url, {})
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['topup_balance_after'], '0.00')
        self.assertEqual(RiderWalletEntry.objects.filter(kind='job_fee').count(), 1)
        self.assertEqual(RiderWalletEntry.objects.get(kind='job_fee').amount, Decimal('-48.00'))

    def test_failure_after_charge_rolls_back_the_whole_acceptance(self):
        self.credit('100.00')
        with patch.object(Session, 'save', side_effect=RuntimeError('Unable to save session')):
            with self.assertRaises(RuntimeError):
                accept_offer(self.rider, self.offer.pk)
        self.assertEqual(RiderInfo.objects.get(user=self.rider).topup_balance, Decimal('100.00'))
        self.assertEqual(RiderWalletEntry.objects.filter(kind='job_fee').count(), 0)
        self.offer.refresh_from_db(); self.assertEqual(self.offer.status, 'pending')

    def test_fee_uses_base_and_fuel_and_rounds_to_cents(self):
        self.assertEqual(acceptance_fee(self.session), Decimal('48.00'))
        self.session.rider_cut = Decimal('0.03'); self.session.fuel_surge_addition = Decimal('0.00')
        self.assertEqual(acceptance_fee(self.session), Decimal('0.01'))

    def test_changed_quote_is_not_charged_without_a_new_confirmation(self):
        self.credit('100.00')
        response = self.client.post(f'/api/riders/jobs/{self.offer.pk}/accept/', {'expected_acceptance_fee': '40.00'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'offer_changed')
        self.assertEqual(RiderInfo.objects.get(user=self.rider).topup_balance, Decimal('100.00'))
        self.assertFalse(RiderWalletEntry.objects.filter(kind='job_fee').exists())

    def test_wallet_is_private_read_only_and_profile_cannot_credit_it(self):
        self.credit('100.00')
        wallet = self.client.get('/api/riders/wallet/')
        self.assertEqual(wallet.data['balance'], '100.00')
        self.assertEqual(len(wallet.data['transactions']), 1)
        self.assertEqual(self.client.post('/api/riders/wallet/', {'amount': '99999'}).status_code, 405)
        self.client.patch('/api/riders/profile/', {'topup_balance': '99999'}, format='json')
        self.assertEqual(RiderInfo.objects.get(user=self.rider).topup_balance, Decimal('100.00'))
        self.client.force_authenticate(self.other_rider)
        self.assertEqual(self.client.get('/api/riders/wallet/').data['transactions'], [])
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get('/api/riders/wallet/').status_code, 403)

    def test_jobs_quote_twenty_percent_and_decline_never_charges(self):
        offer = self.client.get('/api/riders/jobs/').data['offers'][0]
        self.assertEqual(offer['acceptance_fee'], '48.00')
        self.assertEqual(offer['acceptance_percent'], 20)
        self.client.post(f'/api/riders/jobs/{self.offer.pk}/decline/', {})
        self.assertEqual(RiderWalletEntry.objects.count(), 0)

    def test_cancelling_a_job_does_not_restore_the_acceptance_fee(self):
        self.credit('100.00')
        accept_offer(self.rider, self.offer.pk)
        response = self.client.post(f'/api/riders/sessions/{self.session.pk}/cancel/', {'reason': 'vehicle_issue'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(RiderInfo.objects.get(user=self.rider).topup_balance, Decimal('52.00'))


@override_settings(GOOGLE_DIRECTIONS_API_KEY='test-key')
class RiderRoadRouteTests(OrderFixtures):
    def setUp(self):
        self.session = create_session(self.vendor.pk, [self.make_order(rider=None).pk], dispatch_enabled=False, offer_to_rider=self.rider.pk)
        self.ready = {'status': 'ready', 'polylines': ['_p~iF~ps|U_ulLnnqC_mqNvxq`@'], 'distance_meters': 1400, 'duration_seconds': 240, 'copyrights': 'Google'}

    def test_repeated_reads_and_identical_run_coordinates_share_one_google_call(self):
        with patch('riders.road_routes._google_directions', return_value=self.ready) as google:
            first = road_route(self.session)
            second = road_route(self.session)
            other = create_session(self.vendor.pk, [self.make_order(rider=None).pk], dispatch_enabled=False)
            third = road_route(other)
        self.assertEqual(first, second); self.assertEqual(first, third)
        google.assert_called_once()
        self.assertEqual(first['status'], 'ready')

    def test_cache_expiry_and_coordinate_changes_fetch_fresh_geometry(self):
        with patch('riders.road_routes._google_directions', return_value=self.ready) as google:
            road_route(self.session)
            RiderRoadRoute.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
            road_route(self.session)
            self.session.vendor.lat += .001
            road_route(self.session)
        self.assertEqual(google.call_count, 3)

    def test_error_is_cached_and_does_not_draw_a_straight_line(self):
        with patch('riders.road_routes._google_directions', side_effect=TimeoutError) as google:
            first = road_route(self.session); second = road_route(self.session)
        google.assert_called_once()
        self.assertEqual(first['polylines'], [])
        self.assertEqual(first, second)

    def test_worker_lease_suppresses_duplicate_paid_requests(self):
        points = ['5.940000,80.580000', '5.950000,80.590000']
        with patch('riders.road_routes._google_directions', return_value=self.ready):
            _cached_segment(points)
        RiderRoadRoute.objects.update(expires_at=timezone.now()-timedelta(seconds=1), lease_until=timezone.now()+timedelta(seconds=20))
        with patch('riders.road_routes._google_directions') as google:
            response = _cached_segment(points)
        google.assert_not_called(); self.assertEqual(response['status'], 'pending')

    def test_large_runs_fetch_one_new_segment_per_request_and_reuse_completed_segments(self):
        stops = [{'lat': 5.95 + i*.001, 'lng': 80.59} for i in range(30)]
        with patch('riders.road_routes.session_stops', return_value=stops), patch('riders.road_routes._google_directions', return_value=self.ready) as google:
            self.assertEqual(road_route(self.session)['status'], 'pending')
            self.assertEqual(google.call_count, 1)
            complete = road_route(self.session)
            self.assertEqual(google.call_count, 2)
            self.assertEqual(complete['status'], 'ready')
            self.assertEqual(len(complete['polylines']), 2)
            self.assertEqual(complete['distance_meters'], 2800)
            road_route(self.session)
            self.assertEqual(google.call_count, 2)

    def test_routing_is_authorized_and_background_jobs_polling_never_calls_google(self):
        client = APIClient(); client.force_authenticate(self.other_rider)
        with patch('riders.road_routes._google_directions', return_value=self.ready) as google:
            self.assertEqual(client.get(f'/api/riders/sessions/{self.session.pk}/route/').status_code, 404)
            client.force_authenticate(self.rider)
            client.get('/api/riders/jobs/'); client.get('/api/riders/jobs/')
            google.assert_not_called()
            self.assertEqual(client.get(f'/api/riders/sessions/{self.session.pk}/route/').data['status'], 'ready')
        google.assert_called_once()

    def test_directions_request_keeps_waypoints_in_order_and_uses_driving(self):
        body = {'status': 'OK', 'routes': [{'overview_polyline': {'points': 'abc'}, 'legs': [
            {'distance': {'value': 1400}, 'duration': {'value': 240}}]}]}
        import io, json
        with patch('riders.road_routes.urlopen', return_value=io.StringIO(json.dumps(body))) as call:
            result = _google_directions(['a', 'b', 'c', 'd'], 'test-key')
        query = parse_qs(urlsplit(call.call_args.args[0]).query)
        self.assertEqual(query['waypoints'], ['b|c'])
        self.assertEqual(query['mode'], ['driving'])
        self.assertEqual(result['distance_meters'], 1400)
