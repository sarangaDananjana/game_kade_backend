from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APIClient

from orders.models import Order, OrderLocation
from orders.test_fixtures import OrderFixtures
from users.models import CustomUser
from .models import JobOffer, RiderInfo, Session, SessionOrder
from .services import create_session, dispatch_due, issue_penalty


class RiderApiTests(OrderFixtures):
    def setUp(self):
        self.client = APIClient()
        for rider, score, window in [(self.rider, '95.00', 90), (self.other_rider, '75.00', 45)]:
            profile = rider.rider_info
            profile.is_available, profile.score, profile.job_acceptance_window = True, Decimal(score), window
            profile.save()
        self.order = self.make_order(rider=None, status='preparing')

    def session(self, **changes):
        data = dict(rider_cut=Decimal('100.00'), service_charge=Decimal('20.00'), fuel_surge_addition=Decimal('10.00'),
                    estimated_minutes=25, dispatch_enabled=True)
        data.update(changes)
        return create_session(self.vendor.pk, [self.order.pk], **data)

    def auth(self, rider=None):
        self.client.force_authenticate(rider or self.rider)

    def accept(self):
        session = self.session()
        offer = session.job_offers.get(status='pending')
        self.auth()
        response = self.client.post(f'/api/riders/jobs/{offer.pk}/accept/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return session

    def test_profile_and_dashboard_require_rider_role_and_never_allow_score_self_editing(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get('/api/riders/profile/').status_code, 403)
        self.auth()
        response = self.client.patch('/api/riders/profile/', {'score': '100.00', 'job_acceptance_window': 600, 'name': 'Rider name'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['score'], '95.00')
        self.assertEqual(response.data['job_acceptance_window'], 90)
        self.assertEqual(self.client.get('/api/riders/dashboard/').status_code, 200)

    def test_offer_uses_the_rider_window_and_expiry_passes_it_to_another_rider(self):
        session = self.session()
        first = session.job_offers.get(status='pending')
        self.assertEqual(first.rider_id, self.rider.pk)
        self.assertEqual(first.acceptance_window, 90)
        dispatch_due(now=first.expires_at + timedelta(seconds=1))
        first.refresh_from_db()
        second = session.job_offers.get(status='pending')
        self.assertEqual(first.status, 'expired')
        self.assertEqual(second.rider_id, self.other_rider.pk)
        self.assertEqual(second.acceptance_window, 45)

    def test_acceptance_binds_shared_orders_and_rejects_other_riders_and_second_acceptance(self):
        session = self.session()
        offer = session.job_offers.get(status='pending')
        self.auth(self.other_rider)
        self.assertEqual(self.client.post(f'/api/riders/jobs/{offer.pk}/accept/', {}, format='json').status_code, 404)
        self.auth()
        self.assertEqual(self.client.post(f'/api/riders/jobs/{offer.pk}/accept/', {}, format='json').status_code, 200)
        self.assertEqual(self.client.post(f'/api/riders/jobs/{offer.pk}/accept/', {}, format='json').status_code, 409)
        self.order.refresh_from_db()
        self.assertEqual(self.order.rider_id, self.rider.pk)

    def test_session_cluster_is_nearest_first_and_verification_secrets_are_absent(self):
        far = OrderLocation.objects.create(user=self.customer, name='Far stop', lat=5.96, lng=80.60, unique_identity='Far gate')
        far_order = self.make_order(rider=None, location=far)
        session = create_session(self.vendor.pk, [self.order.pk, far_order.pk], dispatch_enabled=True)
        offer = session.job_offers.get(status='pending')
        self.auth()
        result = self.client.get('/api/riders/jobs/').data['offers'][0]['session']
        self.assertLess(result['stops'][0]['distance_from_vendor'], result['stops'][1]['distance_from_vendor'])
        self.assertNotIn('delivery_code', str(result))
        self.assertNotIn('delivery_pin', str(result))
        self.assertNotIn(str(self.order.delivery_code), str(result))

    def test_pin_and_qr_verification_require_pickup_and_the_customer_code(self):
        session = self.accept()
        url = f'/api/riders/sessions/{session.pk}/orders/{self.order.pk}/verify/'
        self.assertEqual(self.client.post(url, {'code': self.order.delivery_pin}, format='json').status_code, 409)
        self.assertEqual(self.client.post(f'/api/riders/sessions/{session.pk}/pickup/', {}, format='json').status_code, 200)
        wrong = '9999' if self.order.delivery_pin != '9999' else '8888'
        self.assertEqual(self.client.post(url, {'code': wrong}, format='json').status_code, 400)
        response = self.client.post(url, {'code': self.order.delivery_pin}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'completed')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'delivered')

    def test_cancellation_records_time_and_profile_counts_then_offers_a_replacement(self):
        session = self.accept()
        response = self.client.post(f'/api/riders/sessions/{session.pk}/cancel/', {'reason': 'vehicle_issue', 'details': 'Puncture'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        profile = RiderInfo.objects.get(user=self.rider)
        self.assertEqual(profile.cancelled_sessions_count, 1)
        self.assertIsNotNone(profile.last_cancelled_at)
        self.assertEqual(profile.penalty_balance, Decimal('0.00'))
        event = profile.cancellations.get()
        self.assertIsNotNone(event.replacement_session_id)
        next_offer = event.replacement_session.job_offers.get(status='pending')
        self.assertEqual(next_offer.rider_id, self.other_rider.pk)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.rider_id)

    def test_single_vendor_binding_and_penalty_idempotency(self):
        from vendors.models import Vendor
        other_vendor = Vendor.objects.create(owner=self.owner, shop_name='Other shop', lat=5.94, lng=80.58)
        other_order = self.make_order(vendor=other_vendor, rider=None)
        admin = CustomUser.objects.create_user('0777773001', role='admin')
        self.client.force_authenticate(admin)
        data = {'vendor': self.vendor.pk, 'orders': [self.order.pk, other_order.pk], 'rider_cut': '100.00', 'service_charge': '20.00'}
        self.assertEqual(self.client.post('/api/riders/admin/sessions/', data, format='json').status_code, 400)
        penalty = {'reference': 'test-rule-001', 'reason': 'Recorded by scoring backend', 'amount': '25.00', 'points': 3}
        url = f'/api/riders/admin/riders/{self.rider.pk}/penalties/'
        self.assertEqual(self.client.post(url, penalty, format='json').status_code, 201)
        self.assertEqual(self.client.post(url, penalty, format='json').status_code, 201)
        profile = RiderInfo.objects.get(user=self.rider)
        self.assertEqual(profile.total_penalties_count, 1)
        self.assertEqual(profile.penalty_balance, Decimal('25.00'))

    def test_score_updates_preserve_history_and_only_change_future_offer_windows(self):
        session = self.session()
        offer = session.job_offers.get(status='pending')
        profile = self.rider.rider_info
        issue_penalty(profile, 'score-update-penalty', 'Review event', Decimal('25.00'), 3)
        admin = CustomUser.objects.create_user('0777773002', role='admin')
        self.client.force_authenticate(admin)
        response = self.client.post(f'/api/riders/admin/riders/{self.rider.pk}/score/', {
            'score': '98.00', 'job_acceptance_window': 120, 'score_version': 'v2',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        profile.refresh_from_db()
        offer.refresh_from_db()
        self.assertEqual(profile.job_acceptance_window, 120)
        self.assertEqual(profile.penalty_balance, Decimal('25.00'))
        self.assertEqual(profile.total_penalties_count, 1)
        self.assertEqual(offer.acceptance_window, 90)
        self.assertEqual(offer.expires_at - offer.offered_at, timedelta(seconds=90))

    def test_dashboard_penalty_dates_use_sri_lankan_midnight(self):
        profile = self.rider.rider_info
        current = issue_penalty(profile, 'current-day', 'Current local day', Decimal('10.00'))
        previous = issue_penalty(profile, 'previous-day', 'Previous local day', Decimal('20.00'))
        current.issued_at = datetime(2026, 11, 1, 19, 0, tzinfo=datetime_timezone.utc)
        previous.issued_at = datetime(2026, 11, 1, 18, 29, tzinfo=datetime_timezone.utc)
        current.save(update_fields=['issued_at'])
        previous.save(update_fields=['issued_at'])
        self.auth()
        now = datetime(2026, 11, 1, 20, 0, tzinfo=datetime_timezone.utc)
        with timezone.override('UTC'), patch('riders.timeutils.timezone.now', return_value=now):
            response = self.client.get('/api/riders/dashboard/?period=today')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['stats']['penalties_count'], 1)
        self.assertEqual(response.data['stats']['penalty_amount'], Decimal('10.00'))

    def test_failed_delivery_can_be_rescheduled_without_changing_its_original_run(self):
        session = self.accept()
        self.client.post(f'/api/riders/sessions/{session.pk}/pickup/', {}, format='json')
        response = self.client.post(f'/api/riders/sessions/{session.pk}/orders/{self.order.pk}/failed/', {
            'reason': 'Customer could not be reached',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'completed')
        self.order.refresh_from_db()
        self.assertIsNone(self.order.rider_id)
        self.assertEqual(self.order.status, 'preparing')
        self.rider.is_active = False
        self.rider.save(update_fields=['is_active'])
        replacement = create_session(self.vendor.pk, [self.order.pk], dispatch_enabled=True)
        offer = replacement.job_offers.get(status='pending')
        self.auth(self.other_rider)
        self.assertEqual(self.client.post(f'/api/riders/jobs/{offer.pk}/accept/', {}, format='json').status_code, 200)
        session.refresh_from_db()
        session.notes = 'Original failure remains in this run.'
        session.save()
        self.assertEqual(session.session_orders.get().status, 'failed')
        self.assertEqual(session.assigned_rider_id, self.rider.pk)

    def test_unbound_historical_sessions_are_not_dispatched(self):
        session = self.session(dispatch_enabled=False)
        Session.objects.filter(pk=session.pk).update(vendor=None, dispatch_enabled=True)
        dispatch_due()
        self.assertFalse(session.job_offers.exists())

    def test_jobs_arrive_and_can_be_accepted_without_going_online(self):
        RiderInfo.objects.all().update(is_available=False)
        session = self.session()
        offer = session.job_offers.get(status='pending')
        self.assertEqual(offer.rider_id, self.rider.pk)
        self.auth()
        self.client.post('/api/riders/availability/', {'is_available': False}, format='json')
        self.assertEqual(self.client.get('/api/riders/jobs/').data['offers'][0]['id'], offer.pk)
        result = self.client.post(f'/api/riders/jobs/{offer.pk}/accept/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)

    def test_profile_tasks_follow_saved_details_and_support_bicycles(self):
        RiderInfo.objects.filter(user=self.rider).update(vehicle_registration='', vehicle_name='', vehicle_colour='', emergency_contact='')
        self.rider.name = None
        self.rider.save(update_fields=['name'])
        self.auth()
        initial = self.client.get('/api/riders/profile/').data['profile_completion']
        self.assertEqual(initial['remaining_tasks'], 3)
        named = self.client.patch('/api/riders/profile/', {'name': 'Saranga'}, format='json')
        self.assertEqual(named.data['profile_completion']['remaining_tasks'], 2)
        bicycle = self.client.patch('/api/riders/profile/', {'vehicle_type': 'bicycle', 'vehicle_name': 'City bicycle', 'vehicle_colour': 'Blue'}, format='json')
        self.assertEqual(bicycle.data['profile_completion']['remaining_tasks'], 1)
        invalid = self.client.patch('/api/riders/profile/', {'emergency_contact': 'invalid'}, format='json')
        self.assertEqual(invalid.status_code, 400)
        complete = self.client.patch('/api/riders/profile/', {'emergency_contact': '+94 77 123 4567'}, format='json')
        self.assertTrue(complete.data['profile_completion']['is_complete'])
        self.assertEqual(complete.data['emergency_contact'], '+94771234567')
        self.assertEqual(self.client.get('/api/riders/dashboard/').data['profile']['profile_completion']['remaining_tasks'], 0)

    def test_managers_can_address_the_initial_offer_to_an_offline_rider(self):
        admin = CustomUser.objects.create_user('0777773003', role='admin')
        self.client.force_authenticate(admin)
        RiderInfo.objects.filter(user=self.other_rider).update(is_available=False)
        result = self.client.post('/api/riders/admin/sessions/', {
            'vendor': self.vendor.pk, 'orders': [self.order.pk], 'rider_cut': '100.00', 'service_charge': '20.00',
            'offer_to_rider': self.other_rider.pk,
        }, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        session = Session.objects.get(pk=result.data['id'])
        self.assertIsNone(session.assigned_rider_id)
        self.assertEqual(session.job_offers.get(status='pending').rider_id, self.other_rider.pk)
