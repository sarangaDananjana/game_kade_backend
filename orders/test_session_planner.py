import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import Client
from django.db import connection
from django.test.utils import CaptureQueriesContext

from riders.models import JobOffer, Session, SessionOrder
from riders.services import RiderActionError, create_session
from users.models import CustomUser
from vendors.models import Vendor

from .models import OrderItem, OrderLocation
from .services.assignment import AssignmentError, assign_orders
from .test_fixtures import ORDER_TIME, OrderFixtures


class SessionPlannerTests(OrderFixtures):
    def setUp(self):
        clock = patch('orders.services.delivery_codes.timezone.now', return_value=ORDER_TIME)
        clock.start()
        self.addCleanup(clock.stop)
        self.admin = CustomUser.objects.create_user('0771000009', role='admin', is_staff=True, is_superuser=True)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin)
        self.order = self.make_order(rider=None)
        OrderItem.objects.create(order=self.order, product=self.product, quantity=1, price_at_purchase=self.product.price)
        self.client.get('/dispatch/plan/')
        self.token = self.client.cookies['csrftoken'].value

    def payload(self, **changes):
        data = {'vendor': self.vendor.pk, 'orders': [self.order.pk], 'rider_cut': '30.00',
                'service_charge': '0.00', 'date': ORDER_TIME.date().isoformat(),
                'dispatch_enabled': False, 'offer_to_rider': self.rider.pk}
        data.update(changes)
        return data

    def post(self, path, data):
        return self.client.post(path, json.dumps(data), content_type='application/json', HTTP_X_CSRFTOKEN=self.token)

    def test_admin_entry_points_open_the_map_and_offers_explain_session_creation(self):
        self.assertContains(self.client.get('/dispatch/plan/'), 'Plan a delivery run')
        self.assertRedirects(self.client.get('/admin/riders/session/add/'), '/dispatch/plan/')
        response = self.client.get('/admin/riders/joboffer/')
        self.assertContains(response, 'Open session map')
        self.assertNotContains(response, 'Add job offer')

    def test_planning_requires_admin_and_writes_require_a_csrf_token(self):
        stranger = Client()
        self.assertEqual(stranger.get('/dispatch/api/planning/').status_code, 403)
        stranger.force_login(self.customer)
        self.assertEqual(stranger.get('/dispatch/api/planning/').status_code, 403)
        self.assertEqual(stranger.post('/dispatch/api/sessions/', '{}', content_type='application/json').status_code, 403)
        response = self.client.post('/dispatch/api/sessions/', json.dumps(self.payload()), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Session.objects.exists())

    def test_map_includes_public_and_private_stops_and_marks_reserved_orders(self):
        public = OrderLocation.objects.create(name='Hostel', is_system_defined=True,
            lat=5.94, lng=80.58, unique_identity='Hostel entrance')
        other_order = self.make_order(rider=None, location=public)
        session = create_session(self.vendor.pk, [other_order.pk])
        response = self.client.get('/dispatch/api/planning/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        orders = {order['id']: order for order in data['orders']}
        self.assertTrue(orders[self.order.pk]['selectable'])
        self.assertEqual(orders[self.order.pk]['location']['name'], 'Blue gate')
        self.assertFalse(orders[other_order.pk]['selectable'])
        self.assertEqual(orders[other_order.pk]['session_id'], session.pk)
        self.assertEqual(orders[other_order.pk]['location']['name'], 'Hostel')
        self.assertIsNotNone(data['delivery_area'])
        self.assertNotIn('delivery_code', response.content.decode())
        self.assertNotIn('delivery_pin', response.content.decode())

    def test_create_links_existing_orders_and_offers_without_accepting_for_the_rider(self):
        second = self.make_order(rider=None, delivery_fee='55.00')
        response = self.post('/dispatch/api/sessions/', self.payload(orders=[self.order.pk, second.pk], rider_cut='85.00'))
        self.assertEqual(response.status_code, 201, response.content)
        session = Session.objects.get()
        self.assertEqual(session.status, 'planned')
        self.assertIsNone(session.assigned_rider_id)
        self.assertEqual(session.rider_revenue, Decimal('85.00'))
        self.assertEqual(session.session_orders.count(), 2)
        self.assertEqual(set(session.session_orders.values_list('delivery_fee', flat=True)), {Decimal('30.00'), Decimal('55.00')})
        offer = session.job_offers.get()
        self.assertEqual(offer.rider_id, self.rider.pk)
        self.assertEqual(offer.acceptance_window, self.rider.rider_info.job_acceptance_window)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.rider_id)

    def test_mixed_kitchens_and_repeat_submissions_do_not_create_partial_sessions(self):
        kitchen = Vendor.objects.create(owner=self.owner, shop_name='Second kitchen', lat=5.94, lng=80.58)
        foreign = self.make_order(rider=None, vendor=kitchen)
        response = self.post('/dispatch/api/sessions/', self.payload(orders=[self.order.pk, foreign.pk]))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(Session.objects.exists())
        self.assertFalse(SessionOrder.objects.exists())
        self.assertEqual(self.post('/dispatch/api/sessions/', self.payload()).status_code, 201)
        response = self.post('/dispatch/api/sessions/', self.payload())
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(Session.objects.count(), 1)
        self.assertEqual(JobOffer.objects.count(), 1)

    def test_unavailable_riders_are_explained_and_rejected_without_reserving_orders(self):
        profile = self.rider.rider_info
        profile.vehicle_colour = ''
        profile.save()
        data = self.client.get('/dispatch/api/planning/').json()
        rider = next(rider for rider in data['riders'] if rider['id'] == self.rider.pk)
        self.assertFalse(rider['eligible'])
        self.assertEqual(rider['reason'], 'Profile incomplete')
        response = self.post('/dispatch/api/sessions/', self.payload())
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(Session.objects.exists())
        self.assertFalse(SessionOrder.objects.exists())

    def test_date_errors_and_historical_orders_cannot_be_silently_rescheduled(self):
        self.assertEqual(self.client.get('/dispatch/api/planning/?date=invalid').status_code, 400)
        yesterday = (ORDER_TIME - timedelta(days=1)).date().isoformat()
        self.assertEqual(self.post('/dispatch/api/sessions/', self.payload(date=yesterday)).status_code, 400)
        older = self.make_order(rider=None, created_at=ORDER_TIME - timedelta(days=1))
        response = self.post('/dispatch/api/sessions/', self.payload(orders=[older.pk]))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(Session.objects.exists())

    def test_planned_only_and_automatic_dispatch_are_supported(self):
        values = self.payload()
        values.pop('offer_to_rider')
        response = self.post('/dispatch/api/sessions/', values)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(JobOffer.objects.exists())
        session = Session.objects.get()
        response = self.post(f'/dispatch/api/sessions/{session.pk}/offer/', {'automatic': True})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(session.job_offers.filter(status='pending').count(), 1)

    def test_expired_offer_can_be_renewed_with_a_new_window_and_retains_history(self):
        session = create_session(self.vendor.pk, [self.order.pk], offer_to_rider=self.rider.pk)
        first = session.job_offers.get()
        original_deadline = first.expires_at
        with patch('orders.services.delivery_codes.timezone.now', return_value=first.expires_at + timedelta(seconds=1)):
            response = self.post(f'/dispatch/api/sessions/{session.pk}/offer/', {'rider_id': self.rider.pk})
        self.assertEqual(response.status_code, 201, response.content)
        first.refresh_from_db()
        self.assertEqual(first.status, 'expired')
        self.assertEqual(first.expires_at, original_deadline)
        self.assertEqual(session.job_offers.count(), 2)
        self.assertEqual(session.job_offers.filter(status='pending').count(), 1)

    def test_live_pending_offer_cannot_be_overwritten_or_duplicated(self):
        session = create_session(self.vendor.pk, [self.order.pk], offer_to_rider=self.rider.pk)
        response = self.post(f'/dispatch/api/sessions/{session.pk}/offer/', {'rider_id': self.other_rider.pk})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(session.job_offers.count(), 1)
        self.assertEqual(session.job_offers.get().rider_id, self.rider.pk)

    def test_legacy_assignment_cannot_overwrite_session_orders(self):
        create_session(self.vendor.pk, [self.order.pk], offer_to_rider=self.rider.pk)
        with self.assertRaises(AssignmentError):
            assign_orders([self.order.pk], self.other_rider.pk)
        with self.assertRaises(AssignmentError):
            assign_orders([self.order.pk], unassign=True)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.rider_id)

    def test_missing_addresses_and_negative_quotes_are_rejected(self):
        missing = self.make_order(rider=None, location=None)
        self.assertEqual(self.post('/dispatch/api/sessions/', self.payload(orders=[missing.pk])).status_code, 409)
        self.assertEqual(self.post('/dispatch/api/sessions/', self.payload(rider_cut='-1.00')).status_code, 400)
        self.assertFalse(Session.objects.exists())

    def test_large_order_groups_do_not_add_database_round_trips_for_every_attempt(self):
        with CaptureQueriesContext(connection) as one:
            create_session(self.vendor.pk, [self.order.pk])
        batch = [self.make_order(rider=None) for _ in range(20)]
        with CaptureQueriesContext(connection) as many:
            session = create_session(self.vendor.pk, [order.pk for order in batch])
        self.assertLessEqual(len(many), len(one) + 5)
        self.assertEqual(session.session_orders.count(), 20)
        self.assertTrue(all(link.order_reference == link.order_id and link.delivery_fee == Decimal('30.00')
                            for link in session.session_orders.all()))

    def test_invalid_source_delivery_fees_cannot_create_partial_sessions(self):
        self.order.delivery_fee = Decimal('-1.00')
        self.order.save(update_fields=['delivery_fee'])
        with self.assertRaises(RiderActionError):
            create_session(self.vendor.pk, [self.order.pk])
        self.assertFalse(Session.objects.exists())
        self.assertFalse(SessionOrder.objects.exists())
