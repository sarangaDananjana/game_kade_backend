from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, override_settings
from rest_framework.test import APIClient

from .models import Order, OrderItem, OrderingWindow
from .ordering_window import current_ordering_window
from .test_delivery_pricing import PricingFixtures


def local_clock(hour, minute=0, second=0):
    return datetime(2026, 11, 10, hour, minute, second, tzinfo=ZoneInfo('Asia/Colombo'))


class OrderingWindowTests(PricingFixtures):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.customer)
        self.window, _ = OrderingWindow.objects.update_or_create(
            pk=1, defaults={'enabled': True, 'opens_at': time(12), 'closes_at': time(17)},
        )
        self.payload = {
            'vendor': self.vendor.pk, 'location': self.far_location.pk,
            'items': [{'product': self.product.pk, 'quantity': 1}],
        }

    def test_default_hours_include_opening_and_exclude_closing(self):
        for clock, expected in [
            (local_clock(11, 59, 59), False), (local_clock(12), True),
            (local_clock(16, 59, 59), True), (local_clock(17), False),
        ]:
            with self.subTest(clock=clock):
                self.assertEqual(current_ordering_window(clock)['is_open'], expected)

    def test_configured_minutes_and_seconds_are_respected(self):
        self.window.opens_at, self.window.closes_at = time(10, 15), time(18, 45, 30)
        self.window.save()
        for clock, expected in [
            (local_clock(10, 14, 59), False), (local_clock(10, 15), True),
            (local_clock(18, 45, 29), True), (local_clock(18, 45, 30), False),
        ]:
            with self.subTest(clock=clock):
                self.assertEqual(current_ordering_window(clock)['is_open'], expected)
        self.assertIn('6:45:30 PM', current_ordering_window(local_clock(19))['message'])

    @override_settings(TIME_ZONE='UTC')
    def test_utc_server_clock_uses_sri_lankan_hours(self):
        utc_clock = local_clock(12).astimezone(ZoneInfo('UTC'))
        self.assertTrue(current_ordering_window(utc_clock)['is_open'])
        self.assertFalse(current_ordering_window(local_clock(17).astimezone(ZoneInfo('UTC')))['is_open'])

    def test_overnight_hours_work_across_midnight(self):
        self.window.opens_at, self.window.closes_at = time(20, 30), time(2, 15)
        self.window.save()
        for clock, expected in [
            (local_clock(20, 29), False), (local_clock(20, 30), True),
            (local_clock(0), True), (local_clock(2, 14, 59), True),
            (local_clock(2, 15), False), (local_clock(12), False),
        ]:
            with self.subTest(clock=clock):
                self.assertEqual(current_ordering_window(clock)['is_open'], expected)
        self.assertIn('next day', current_ordering_window(local_clock(12))['hours_display'])

    def test_all_checkout_paths_reject_closed_orders_without_writes(self):
        for url in ['/api/orders/', '/api/orders/create/', '/shop/orders/create/']:
            with self.subTest(url=url), patch('orders.ordering_window.timezone.now', return_value=local_clock(17)):
                response = self.client.post(url, self.payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(response.data['code'], 'ordering_closed')
                self.assertIn('5:00 PM', response.data['detail'])
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OrderItem.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 100)

    def test_changing_hours_applies_to_next_checkout_on_every_path(self):
        self.window.closes_at = time(19, 30)
        self.window.save()
        for url in ['/api/orders/', '/api/orders/create/', '/shop/orders/create/']:
            with self.subTest(url=url), patch('orders.ordering_window.timezone.now', return_value=local_clock(18)):
                response = self.client.post(url, self.payload, format='json')
                self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Order.objects.count(), 3)
        self.window.closes_at = time(17)
        self.window.save()
        with patch('orders.ordering_window.timezone.now', return_value=local_clock(18)):
            response = self.client.post('/api/orders/create/', self.payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 3)

    def test_pausing_blocks_checkout_during_normal_hours(self):
        self.window.enabled = False
        self.window.save()
        with patch('orders.ordering_window.timezone.now', return_value=local_clock(14)):
            response = self.client.post('/api/orders/create/', self.payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'ordering_closed')
        self.assertIn('temporarily paused', response.data['detail'])
        self.assertFalse(Order.objects.exists())

    def test_public_status_matches_both_paths_and_cannot_be_modified(self):
        self.client.force_authenticate(None)
        with patch('orders.ordering_window.timezone.now', return_value=local_clock(14)):
            canonical = self.client.get('/api/orders/ordering-window/')
            compatible = self.client.get('/shop/orders/ordering-window/')
        self.assertEqual(canonical.status_code, 200)
        self.assertEqual(canonical.data, compatible.data)
        self.assertEqual(canonical['Cache-Control'], 'no-store')
        self.assertEqual(canonical.data['timezone'], 'Asia/Colombo')
        self.assertEqual(canonical.data['closes_at'], '17:00:00')
        self.assertTrue(canonical.data['is_open'])
        for method in [self.client.post, self.client.patch, self.client.put, self.client.delete]:
            self.assertIn(method('/api/orders/ordering-window/', {'enabled': False}, format='json').status_code, [401, 403, 405])
        self.window.refresh_from_db()
        self.assertTrue(self.window.enabled)

    def test_existing_orders_can_be_viewed_and_cancelled_after_closing(self):
        order = self.make_order(status='pending')
        with patch('orders.ordering_window.timezone.now', return_value=local_clock(18)):
            self.assertEqual(self.client.get('/api/orders/').status_code, 200)
            response = self.client.patch(f'/api/orders/{order.pk}/', {'status': 'cancelled'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_equal_times_are_rejected_by_admin_validation_and_database(self):
        self.window.closes_at = self.window.opens_at
        with self.assertRaises(ValidationError):
            self.window.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.window.save()

    def test_database_prevents_more_than_one_settings_row(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderingWindow.objects.create(pk=2)

    def test_default_schedule_is_used_if_settings_row_is_missing(self):
        OrderingWindow.objects.all().delete()
        self.assertTrue(current_ordering_window(local_clock(12))['is_open'])
        self.assertFalse(current_ordering_window(local_clock(17))['is_open'])
        self.assertFalse(OrderingWindow.objects.exists())

    def test_admin_can_edit_but_cannot_add_or_delete_schedule(self):
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save()
        request = RequestFactory().get('/admin/')
        request.user = self.owner
        model_admin = admin.site._registry[OrderingWindow]
        self.assertTrue(model_admin.has_change_permission(request, self.window))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request, self.window))
        self.client.force_login(self.owner)
        url = '/admin/orders/orderingwindow/1/change/'
        response = self.client.post(url, {
            'enabled': 'on', 'opens_at': '09:30:00', 'closes_at': '19:15:00', '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        self.window.refresh_from_db()
        self.assertEqual(self.window.opens_at, time(9, 30))
        self.assertEqual(self.window.closes_at, time(19, 15))
        self.assertTrue(current_ordering_window(local_clock(18))['is_open'])
