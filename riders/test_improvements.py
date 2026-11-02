from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import Client
from rest_framework.test import APIClient

from orders.models import Order, OrderLocation
from orders.services.assignment import AssignmentError, assign_orders
from orders.test_fixtures import OrderFixtures
from .admin_services import update_session_from_admin
from .models import RiderInfo, Session
from .profile import profile_completion
from .routes import session_stops
from .services import RiderActionError, create_session, verify_delivery


class RiderImprovementsTests(OrderFixtures):
    def setUp(self):
        self.order = self.make_order(rider=None)
        self.client = APIClient()
        self.client.force_authenticate(self.rider)

    def test_incomplete_profiles_receive_no_offers_and_cannot_accept_stale_offers(self):
        session = create_session(self.vendor.pk, [self.order.pk], offer_to_rider=self.rider.pk)
        offer = session.job_offers.get(status='pending')
        RiderInfo.objects.filter(user=self.rider).update(vehicle_colour='')
        self.assertEqual(self.client.get('/api/riders/jobs/').data['offers'], [])
        response = self.client.post(f'/api/riders/jobs/{offer.pk}/accept/', {}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'profile_incomplete')
        session.refresh_from_db()
        self.assertIsNone(session.assigned_rider_id)
        self.assertEqual(session.job_offers.get().status, 'pending')

    def test_dispatch_and_legacy_assignment_skip_incomplete_riders(self):
        RiderInfo.objects.all().update(vehicle_name='')
        session = create_session(self.vendor.pk, [self.order.pk], dispatch_enabled=True)
        self.assertFalse(session.job_offers.exists())
        with self.assertRaises(RiderActionError):
            create_session(self.vendor.pk, [self.make_order(rider=None).pk], offer_to_rider=self.rider.pk)
        with self.assertRaises(AssignmentError):
            assign_orders([self.order.pk], self.rider.pk)

    def test_vehicle_number_alone_and_a_bicycle_without_model_or_colour_are_incomplete(self):
        profile = self.rider.rider_info
        profile.vehicle_name = ''
        self.assertFalse(profile_completion(profile)['is_complete'])
        profile.vehicle_type, profile.vehicle_registration = 'bicycle', ''
        self.assertFalse(profile_completion(profile)['is_complete'])
        profile.vehicle_name = 'City bicycle'
        self.assertTrue(profile_completion(profile)['is_complete'])

    def test_chart_spans_a_monday_and_keeps_the_same_days_for_every_filter(self):
        for period in ['week', 'month', 'lifetime']:
            with patch('riders.views.rider_local_date', return_value=date(2026, 11, 2)):
                response = self.client.get(f'/api/riders/dashboard/?period={period}')
            self.assertEqual(response.status_code, 200)
            dates = [str(day['date']) for day in response.data['earnings_series']]
            self.assertEqual(dates, ['2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02'])

    def test_private_locations_at_identical_coordinates_stay_separate_and_public_locations_group(self):
        public = OrderLocation.objects.create(name='Shared entrance', unique_identity='Shared entrance', is_system_defined=True,
            lat=self.location.lat, lng=self.location.lng, description='Meet at the entrance')
        second = OrderLocation.objects.create(name='Private label', user=self.customer, unique_identity='Amber Owl',
            lat=self.location.lat, lng=self.location.lng)
        orders = [self.order, self.make_order(rider=None, location=second), self.make_order(rider=None, location=public),
                  self.make_order(rider=None, location=public, user=self.owner)]
        session = create_session(self.vendor.pk, [o.pk for o in orders])
        stops = session_stops(session)
        self.assertEqual(len(stops), 3)
        self.assertEqual(next(s for s in stops if s['location_id']==second.pk)['name'], 'Amber Owl')
        shared = next(s for s in stops if s['location_id']==public.pk)
        self.assertEqual(shared['name'], 'Shared entrance')
        self.assertEqual(len(shared['orders']), 2)
        payload = stops[0]['orders'][0]
        self.assertFalse(payload['number'].startswith('#'))
        self.assertEqual(len(payload['unique_identity'].split()), 2)
        self.assertEqual(Decimal(payload['order_value']) + Decimal(payload['delivery_fee']), Decimal(payload['total_amount']))
        self.assertNotIn('delivery_code', payload)
        self.assertNotIn('delivery_pin', payload)

    def test_order_name_survives_changes_and_name_collisions_retry(self):
        identity = self.order.unique_identity
        self.order.status = 'pending'
        self.order.save(update_fields=['status'])
        self.assertEqual(Order.objects.get(pk=self.order.pk).unique_identity, identity)
        other = self.make_order(unique_identity=identity)
        self.assertNotEqual(other.unique_identity, identity)

    def test_admin_assignment_pickup_and_finances_keep_orders_in_sync(self):
        session = create_session(self.vendor.pk, [self.order.pk])
        update_session_from_admin(session.pk, rider_id=self.rider.pk, status='accepted', finance_status='draft')
        self.order.refresh_from_db()
        self.assertEqual(self.order.rider_id, self.rider.pk)
        update_session_from_admin(session.pk, rider_id=self.other_rider.pk, status='accepted', finance_status='draft')
        self.order.refresh_from_db()
        self.assertEqual(self.order.rider_id, self.other_rider.pk)
        session = update_session_from_admin(session.pk, rider_id=self.other_rider.pk, status='in_progress', finance_status='draft')
        self.assertIsNotNone(session.started_at)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'out_for_delivery')
        with self.assertRaises(RiderActionError):
            update_session_from_admin(session.pk, rider_id=self.other_rider.pk, status='completed', finance_status='confirmed')
        verify_delivery(self.other_rider, session.pk, self.order.pk, self.order.delivery_pin)
        session = update_session_from_admin(session.pk, rider_id=self.other_rider.pk, status='completed', finance_status='confirmed')
        self.assertIsNotNone(session.settled_at)
        with self.assertRaises(RiderActionError):
            update_session_from_admin(session.pk, rider_id=self.other_rider.pk, status='completed', finance_status='draft')

    def test_invalid_admin_finances_roll_back_rider_and_status_changes(self):
        session = create_session(self.vendor.pk, [self.order.pk])
        with self.assertRaises(RiderActionError):
            update_session_from_admin(session.pk, rider_id=self.rider.pk, status='accepted', finance_status='confirmed')
        session.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(session.status, 'planned')
        self.assertIsNone(self.order.rider_id)

    def test_admin_list_forms_and_status_save_are_usable(self):
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save(update_fields=['is_staff', 'is_superuser'])
        client = Client()
        client.force_login(self.owner)
        session = create_session(self.vendor.pk, [self.order.pk])
        response = client.get('/admin/riders/session/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="session-row-{session.pk}"')
        self.assertContains(response, f'form="session-row-{session.pk}"')
        response = client.post(f'/admin/riders/session/{session.pk}/update-row/', {'rider_id':self.rider.pk, 'status':'accepted', 'finance_status':'draft'})
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.status, 'accepted')
