import uuid
import importlib
from datetime import datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.contrib.gis.geos import Polygon
from orders.models import DeliveryZone as MapDeliveryZone
from django.apps import apps
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from users.models import CustomUser
from .models import Order, OrderLocation, Product, Vendor
from .services.delivery_codes import delivery_code_matches, delivery_code_prefix, generate_delivery_code


ORDER_TIME = datetime(2026, 11, 1, 8, 35, tzinfo=datetime_timezone.utc)


class DeliveryCodeValidationTests(SimpleTestCase):
    def test_date_prefix_changes_at_sri_lankan_midnight(self):
        self.assertEqual(delivery_code_prefix(datetime(2026, 11, 1, 18, 29, 59, tzinfo=datetime_timezone.utc)), '261101')
        self.assertEqual(delivery_code_prefix(datetime(2026, 11, 1, 18, 30, tzinfo=datetime_timezone.utc)), '261102')

    def test_confirmation_accepts_the_exact_qr_or_four_digits(self):
        legacy = uuid.uuid4()
        order = SimpleNamespace(delivery_code='2611010042', legacy_delivery_code=legacy)
        for code in ['0042', '2611010042', str(legacy), ' 0042 ']:
            with self.subTest(code=code):
                self.assertTrue(delivery_code_matches(order, code))
        for code in [None, '', '042', 42, True, [], '2610310042', '2611010043', '４８２１', 'ආහාර', 'ABCD']:
            with self.subTest(code=code):
                self.assertFalse(delivery_code_matches(order, code))


class OrderFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Synthetic service boundary for the existing order and pricing API fixtures.
        MapDeliveryZone.objects.create(name='Test Matara service area', district='Matara', is_primary=True,
            polygon=Polygon(((80.3, 5.7), (80.9, 5.7), (80.9, 6.3), (80.3, 6.3), (80.3, 5.7)), srid=4326))
        cls.customer = CustomUser.objects.create_user('0771000001', name='Customer')
        cls.owner = CustomUser.objects.create_user('0771000002', role='owner')
        cls.rider = CustomUser.objects.create_user('0771000003', role='rider')
        cls.other_rider = CustomUser.objects.create_user('0771000004', role='rider')
        cls.vendor = Vendor.objects.create(owner=cls.owner, shop_name='Dinner kitchen', lat=5.938075, lng=80.581056)
        cls.location = OrderLocation.objects.create(user=cls.customer, name='Boarding', lat=5.938075, lng=80.581056, unique_identity='Blue gate')
        cls.product = Product.objects.create(vendor=cls.vendor, name='Rice and curry', price='320.00', stock_quantity=100)

    def make_order(self, **changes):
        data = dict(user=self.customer, vendor=self.vendor, location=self.location,
                    rider=self.rider, total_amount='350.00', delivery_fee='30.00',
                    status='preparing', created_at=ORDER_TIME)
        data.update(changes)
        return Order.objects.create(**data)


class DeliveryCodeCreationTests(OrderFixtures):
    def test_every_order_gets_a_unique_date_and_random_number(self):
        orders = [self.make_order() for _ in range(100)]
        codes = {order.delivery_code for order in orders}
        self.assertEqual(len(codes), 100)
        self.assertTrue(all(len(code) == 10 and code.isdigit() and code.startswith('261101') for code in codes))

    def test_leading_zeroes_and_code_stability(self):
        with patch('multivender.services.delivery_codes.secrets.choice', return_value='2611010042'):
            order = self.make_order()
        self.assertEqual(order.delivery_pin, '0042')
        order.status = 'out_for_delivery'
        order.save(update_fields=['status'])
        order.refresh_from_db()
        self.assertEqual(order.delivery_code, '2611010042')

    def test_concurrent_code_collision_retries_without_breaking_checkout_transaction(self):
        self.make_order(delivery_code='2611010042')
        with patch('multivender.models.generate_delivery_code', side_effect=['2611010042', '2611010043']):
            order = self.make_order()
        self.assertEqual(order.delivery_code, '2611010043')
        self.assertEqual(Order.objects.count(), 2)

    def test_capacity_exhaustion_does_not_reuse_an_order_code(self):
        for suffix in range(3):
            self.make_order(delivery_code=f'261101{suffix:04d}')
        with patch('multivender.services.delivery_codes.DAILY_CODE_CAPACITY', 3):
            with self.assertRaises(ValidationError):
                generate_delivery_code(created_at=ORDER_TIME)

    def test_database_rejects_invalid_order_numbers(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_order(delivery_code='not-a-code')

    def test_date_matches_the_record_when_saved_after_midnight(self):
        timestamp = datetime(2026, 11, 1, 18, 29, 59, tzinfo=datetime_timezone.utc)
        order = Order(user=self.customer, vendor=self.vendor, location=self.location,
                      total_amount='350.00', created_at=timestamp)
        with patch('multivender.services.delivery_codes.timezone.now', return_value=datetime(2026, 11, 1, 18, 30, tzinfo=datetime_timezone.utc)):
            order.save()
        self.assertTrue(order.delivery_code.startswith('261101'))
        self.assertEqual(order.created_at, timestamp)


class ExistingSchemaCompatibilityTests(OrderFixtures):
    def test_compatibility_migrations_preserve_already_provisioned_schema_and_data(self):
        self.vendor.district = 'Matara'
        self.vendor.save(update_fields=['district'])
        editor = SimpleNamespace(connection=connection, add_field=Mock(), create_model=Mock(), alter_field=Mock())
        for module, function in [
            ('users.migrations.0003_ensure_user_schema', 'ensure_user_schema'),
            ('orders.migrations.0002_complete_delivery_schema', 'ensure_delivery_schema'),
            ('multivender.migrations.0005_complete_location_schema', 'ensure_location_schema'),
        ]:
            getattr(importlib.import_module(module), function)(apps, editor)
        editor.add_field.assert_not_called()
        editor.create_model.assert_not_called()
        editor.alter_field.assert_not_called()
        self.vendor.refresh_from_db()
        self.rider.refresh_from_db()
        self.assertEqual(self.vendor.district, 'Matara')
        self.assertEqual(self.rider.role, 'rider')


@override_settings(MONGO_URI=None)
class HandoverApiTests(OrderFixtures):
    def setUp(self):
        self.client = APIClient()
        self.order = self.make_order(delivery_code='2611010042')
        self.url = f'/shop/rider/orders/{self.order.pk}/'

    def confirm(self, code='0042', user=None, status='delivered'):
        self.client.force_authenticate(user=user or self.rider)
        return self.client.patch(self.url, {'status': status, 'delivery_code': code}, format='json')

    def test_four_digits_complete_delivery(self):
        response = self.confirm()
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'delivered')
        self.assertEqual(response.data['delivery_code'], '2611010042')
        self.assertEqual(response.data['delivery_pin'], '0042')

    def test_full_qr_number_completes_delivery(self):
        self.assertEqual(self.confirm('2611010042').status_code, 200)

    def test_previously_issued_uuid_qr_still_completes_delivery(self):
        self.assertEqual(self.confirm(str(self.order.legacy_delivery_code)).status_code, 200)

    def test_wrong_or_incomplete_code_does_not_change_status(self):
        for code in ['0043', '2610310042', '042', '', None, 42, '４８２１']:
            with self.subTest(code=code):
                self.assertEqual(self.confirm(code).status_code, 400)
                self.order.refresh_from_db()
                self.assertEqual(self.order.status, 'preparing')

    def test_only_the_assigned_rider_can_confirm(self):
        self.assertEqual(self.confirm(user=self.customer).status_code, 403)
        self.assertEqual(self.confirm(user=self.other_rider).status_code, 404)
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.patch(self.url, {'status': 'delivered', 'delivery_code': '0042'}, format='json').status_code, [401, 403])

    def test_delivered_order_cannot_be_delivered_again_or_reopened(self):
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(self.confirm().status_code, 400)
        self.assertEqual(self.confirm(status='out_for_delivery').status_code, 400)

    def test_cancelled_order_cannot_be_handed_over(self):
        self.order.status = 'cancelled'
        self.order.save(update_fields=['status'])
        self.assertEqual(self.confirm().status_code, 404)

    def test_customer_response_has_full_qr_and_short_code(self):
        self.client.force_authenticate(user=self.customer)
        response = self.client.get(f'/shop/orders/{self.order.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['qr_payload'], '2611010042')
        self.assertEqual(response.data['delivery_pin'], '0042')
        self.assertNotIn('legacy_delivery_code', response.data)

    def test_new_codes_are_generated_by_backend_and_cannot_be_chosen_by_customer(self):
        self.client.force_authenticate(user=self.customer)
        with patch('multivender.views.timezone.now', return_value=ORDER_TIME), patch('multivender.services.pricing.calculate_delivery_fee', return_value=30):
            response = self.client.post('/shop/orders/create/', {
                'vendor': self.vendor.pk, 'location': self.location.pk,
                'delivery_code': '0000000000', 'delivery_pin': '0000',
                'items': [{'product': self.product.pk, 'quantity': 1}],
            }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(response.data['delivery_code'], '0000000000')
        self.assertEqual(response.data['qr_payload'], response.data['delivery_code'])
        self.assertEqual(response.data['delivery_pin'], response.data['delivery_code'][-4:])

    def test_failed_items_do_not_leave_an_orphan_order(self):
        from .serializers import OrderSerializer
        serializer = OrderSerializer(data={
            'vendor': self.vendor.pk, 'location': self.location.pk,
            'items': [{'product': self.product.pk, 'quantity': 1}],
        }, context={'request': SimpleNamespace(user=self.customer)})
        serializer.is_valid(raise_exception=True)
        count = Order.objects.count()
        with patch('multivender.services.pricing.calculate_delivery_fee', return_value=30), patch('multivender.serializers.OrderItem.objects.create', side_effect=RuntimeError('item write failed')):
            with self.assertRaises(RuntimeError):
                serializer.save(user=self.customer)
        self.assertEqual(Order.objects.count(), count)

    def test_rider_list_reads_mealdrop_orders_and_excludes_other_assignments(self):
        from orders.models import Order as LegacyOrder
        LegacyOrder.objects.create(id=self.order.pk, user=self.customer, rider=self.rider, total_amount='100.00')
        self.make_order(rider=self.other_rider)
        self.make_order(status='cancelled')
        self.client.force_authenticate(user=self.rider)
        with patch('orders.views.timezone.now', return_value=ORDER_TIME):
            response = self.client.get('/shop/rider/orders/with-rank/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([order['id'] for order in response.data], [self.order.pk])
        self.assertEqual(response.data[0]['shop_name'], 'Dinner kitchen')
        self.assertEqual(response.data[0]['rank'], None)

    def test_legacy_rider_endpoint_still_accepts_its_original_uuid(self):
        from orders.models import Order as LegacyOrder
        legacy = LegacyOrder.objects.create(user=self.customer, rider=self.rider, total_amount='100.00')
        self.client.force_authenticate(user=self.rider)
        response = self.client.patch(f'/api/orders/rider/orders/{legacy.pk}/', {'status': 'delivered', 'delivery_code': str(legacy.delivery_code)}, format='json')
        self.assertEqual(response.status_code, 200)

    @override_settings(MONGO_URI='mongodb://unused.test')
    def test_route_optimization_and_ranks_use_the_mealdrop_collection(self):
        document = {'rider_id': self.rider.pk, 'date': '2026-11-01', 'route_points': [
            {'order_id': self.order.pk, 'lat': self.location.lat, 'lng': self.location.lng, 'rank': 1},
        ]}
        self.client.force_authenticate(user=self.rider)
        with patch('orders.views.timezone.now', return_value=ORDER_TIME), patch('pymongo.MongoClient') as mongo, patch('requests.get') as google:
            collection = mongo.return_value.__getitem__.return_value.__getitem__.return_value
            collection.find_one.return_value = document
            google.return_value.json.return_value = {'status': 'OK', 'routes': [{'waypoint_order': [0]}]}
            optimized = self.client.post('/shop/rider/routes/optimize/', {'lat': 5.94, 'lng': 80.58}, format='json')
            self.assertEqual(optimized.status_code, 200, optimized.data)
            saved = collection.update_one.call_args.args[1]['$set']
            self.assertEqual([point['order_id'] for point in saved['route_points']], [self.order.pk])
            ranked = self.client.get('/shop/rider/orders/with-rank/')
            self.assertEqual(ranked.data[0]['rank'], 1)
            current = self.client.get('/shop/rider/routes/current/')
            self.assertEqual(current.status_code, 200)
            mongo.return_value.__getitem__.return_value.__getitem__.assert_called_with('multivendor_route_states')


class DeliveryCodeMigrationTests(TransactionTestCase):
    def test_existing_orders_get_unique_numeric_codes_and_rollback_preserves_uuids(self):
        before = [('multivender', '0003_h3_spatial_pricing'), ('users', '0003_ensure_user_schema')]
        after = [('multivender', '0004_numeric_delivery_codes'), ('users', '0003_ensure_user_schema')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            old_apps = executor.loader.project_state(before).apps
            User = old_apps.get_model('users', 'CustomUser')
            VendorModel = old_apps.get_model('multivender', 'Vendor')
            OldOrder = old_apps.get_model('multivender', 'Order')
            user = User.objects.create(phone_number='0772000001')
            vendor = VendorModel.objects.create(owner=user, shop_name='Existing kitchen', lat=5.94, lng=80.58)
            expected = {}
            for timestamp in [ORDER_TIME, ORDER_TIME, datetime(2026, 10, 31, 18, 29, tzinfo=datetime_timezone.utc)]:
                order = OldOrder.objects.create(user=user, vendor=vendor, total_amount='320.00')
                OldOrder.objects.filter(pk=order.pk).update(created_at=timestamp)
                expected[order.pk] = (order.delivery_code, timestamp)
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            NewOrder = executor.loader.project_state(after).apps.get_model('multivender', 'Order')
            codes = set()
            for order in NewOrder.objects.all():
                legacy_code, timestamp = expected[order.pk]
                self.assertEqual(order.legacy_delivery_code, legacy_code)
                self.assertTrue(order.delivery_code.startswith(delivery_code_prefix(timestamp)))
                self.assertEqual(len(order.delivery_code), 10)
                codes.add(order.delivery_code)
            self.assertEqual(len(codes), len(expected))
            executor = MigrationExecutor(connection)
            executor.migrate(before)
            RestoredOrder = executor.loader.project_state(before).apps.get_model('multivender', 'Order')
            for order in RestoredOrder.objects.all():
                self.assertEqual(order.delivery_code, expected[order.pk][0])
        finally:
            MigrationExecutor(connection).migrate(latest)
