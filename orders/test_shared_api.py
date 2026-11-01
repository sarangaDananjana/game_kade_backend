from unittest.mock import patch

from django.apps import apps
from rest_framework.test import APIClient

from products.models import Product
from users.models import CustomUser
from vendors.models import Vendor
from .models import Order, OrderLocation
from .test_delivery_pricing import PricingFixtures
from .test_fixtures import ORDER_TIME


class SharedApiTests(PricingFixtures):
    def setUp(self):
        self.client = APIClient()

    def test_both_vendor_api_paths_and_product_api_use_the_same_menu(self):
        canonical = self.client.get('/api/vendors/').data
        compatible = self.client.get('/shop/vendors/').data
        self.assertEqual(canonical, compatible)
        self.assertEqual(canonical[0]['products'][0]['id'], self.product.pk)
        products = self.client.get(f'/api/products/?vendor={self.vendor.pk}').data
        self.assertEqual(products[0]['id'], self.product.pk)
        self.assertEqual(products[0]['vendor'], self.vendor.pk)
        self.assertEqual(products[0]['price'], canonical[0]['products'][0]['price'])

    def test_product_updates_are_shared_and_limited_to_the_vendor_owner(self):
        self.client.force_authenticate(self.customer)
        url = f'/api/products/{self.product.pk}/'
        self.assertEqual(self.client.patch(url, {'price': '1.00'}, format='json').status_code, 403)
        other_owner = CustomUser.objects.create_user('0774500001', role='owner')
        other_vendor = Vendor.objects.create(owner=other_owner, shop_name='Other kitchen', lat=5.94, lng=80.58)
        self.client.force_authenticate(other_owner)
        self.assertEqual(self.client.patch(url, {'price': '1.00'}, format='json').status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.patch(url, {'vendor': other_vendor.pk}, format='json').status_code, 400)
        response = self.client.patch(url, {'price': '250.00'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f'/api/vendors/{self.vendor.pk}/').data['products'][0]['price'], '250.00')
        self.assertEqual(self.client.get(f'/shop/vendors/{self.vendor.pk}/').data['products'][0]['price'], '250.00')

    def test_checkout_and_cancellation_share_records_across_api_paths(self):
        self.client.force_authenticate(self.customer)
        payload = {'vendor': self.vendor.pk, 'location': self.far_location.pk,
                   'items': [{'product': self.product.pk, 'quantity': 2}], 'expected_total_amount': '640.00'}
        with patch('orders.views.timezone.now', return_value=ORDER_TIME):
            created = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        order_id = created.data['id']
        canonical = self.client.get(f'/api/orders/{order_id}/')
        compatible = self.client.get(f'/shop/orders/{order_id}/')
        self.assertEqual(canonical.data, compatible.data)
        self.assertEqual(canonical.data['location'], self.far_location.pk)
        self.assertEqual(canonical.data['delivery_fee'], '240.00')
        self.assertEqual(self.client.get('/api/orders/').data, self.client.get('/shop/orders/').data)
        cancelled = self.client.patch(f'/shop/orders/{order_id}/', {'status': 'cancelled', 'total_amount': '1.00'}, format='json')
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(self.client.get(f'/api/orders/{order_id}/').data['status'], 'cancelled')
        self.assertEqual(Order.objects.get(pk=order_id).total_amount, 640)
        self.assertEqual(Order.objects.count(), 1)

    def test_location_api_paths_share_saved_addresses(self):
        self.client.force_authenticate(self.customer)
        created = self.client.post('/api/orders/locations/', {
            'name': 'My entrance', 'lat': self.location.lat, 'lng': self.location.lng,
            'unique_identity': 'Blue entrance', 'is_system_defined': True, 'user': self.owner.pk,
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertFalse(created.data['is_system_defined'])
        self.assertEqual(OrderLocation.objects.get(pk=created.data['id']).user_id, self.customer.pk)
        self.assertEqual(self.client.get('/api/orders/locations/').data, self.client.get('/shop/locations/').data)
        updated = self.client.patch(f"/shop/locations/{created.data['id']}/", {'name': 'Renamed entrance'}, format='json')
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.client.get(f"/api/orders/locations/{created.data['id']}/").data['name'], 'Renamed entrance')

    def test_shared_system_addresses_cannot_be_changed_by_customers(self):
        self.client.force_authenticate(self.customer)
        for prefix in ['/shop/locations/', '/api/orders/locations/']:
            self.assertEqual(self.client.patch(f'{prefix}{self.far_location.pk}/', {'name': 'Changed'}, format='json').status_code, 404)
            self.assertEqual(self.client.delete(f'{prefix}{self.far_location.pk}/').status_code, 404)
        self.far_location.refresh_from_db()
        self.assertEqual(self.far_location.name, 'Tier four')

    def test_vendor_quotes_do_not_accept_another_customers_saved_address(self):
        self.client.force_authenticate(self.other_rider)
        response = self.client.get(f'/api/vendors/?order_location_id={self.location.pk}')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/vendors/?order_location_id=invalid').status_code, 400)

    def test_daily_summary_keeps_identically_named_meals_from_different_kitchens_separate(self):
        self.client.force_authenticate(CustomUser.objects.create_user('0774500002', role='admin'))
        other_vendor = Vendor.objects.create(owner=self.owner, shop_name='Second kitchen', lat=5.94, lng=80.58)
        other_product = Product.objects.create(vendor=other_vendor, name=self.product.name, price='200.00')
        from .models import OrderItem
        for vendor, product, quantity in [(self.vendor, self.product, 2), (other_vendor, other_product, 3)]:
            order = self.make_order(vendor=vendor, created_at=ORDER_TIME)
            OrderItem.objects.create(order=order, product=product, quantity=quantity, price_at_purchase=product.price)
        with patch('orders.views.timezone.now', return_value=ORDER_TIME):
            response = self.client.get('/api/orders/daily-summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row['vendor_id']: row['total_quantity'] for row in response.data}, {self.vendor.pk: 2, other_vendor.pk: 3})

    def test_registered_models_have_one_owner_and_compatibility_imports_match(self):
        from multivender.models import Order as CompatibleOrder, Product as CompatibleProduct
        self.assertIs(CompatibleOrder, Order)
        self.assertIs(CompatibleProduct, Product)
        self.assertEqual(list(apps.get_app_config('multivender').get_models()), [])

    def test_existing_multi_vendor_admin_bookmarks_redirect_to_the_owning_apps(self):
        response = self.client.get('/admin/multivender/product/7/change/?next=/dispatch/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/admin/products/product/7/change/?next=/dispatch/')
        self.assertEqual(self.client.get('/admin/multivender/vendor/').get('Location'), '/admin/vendors/vendor/')

    def test_dispatch_and_zone_changes_require_admin_and_ignore_finished_orders(self):
        self.assertEqual(self.client.get('/api/orders/delivery-zones/').status_code, 200)
        self.assertIn(self.client.get('/api/orders/daily-summary/').status_code, [401, 403])
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.post('/api/orders/bulk-assign/', {'order_ids': [1], 'rider_id': self.rider.pk}, format='json').status_code, 403)
        self.assertEqual(self.client.post('/api/orders/delivery-zones/', {}, format='json').status_code, 403)
        admin = CustomUser.objects.create_user('0774500003', role='admin')
        self.client.force_authenticate(admin)
        active = self.make_order(rider=None)
        delivered = self.make_order(rider=self.other_rider, status='delivered')
        cancelled = self.make_order(rider=self.other_rider, status='cancelled')
        response = self.client.post('/api/orders/bulk-assign/', {
            'order_ids': [active.pk, delivered.pk, cancelled.pk], 'rider_id': self.rider.pk,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        active.refresh_from_db()
        delivered.refresh_from_db()
        cancelled.refresh_from_db()
        self.assertEqual(active.rider_id, self.rider.pk)
        self.assertEqual(delivered.rider_id, self.other_rider.pk)
        self.assertEqual(cancelled.rider_id, self.other_rider.pk)
        self.assertEqual(self.client.post('/api/orders/bulk-assign/', {'order_ids': ['invalid'], 'rider_id': self.rider.pk}, format='json').status_code, 400)

    def test_browser_admin_maps_use_vendor_data_and_support_csrf_protected_zone_creation(self):
        admin = CustomUser.objects.create_user('0774500004', password='test-password', role='admin')
        browser = APIClient(enforce_csrf_checks=True)
        self.assertTrue(browser.login(phone_number=admin.phone_number, password='test-password'))
        response = browser.get('/api/orders/admin/delivery-zones/map/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['vendors'][0]['id'], self.vendor.pk)
        self.assertEqual(response.context['vendors'][0]['shop_name'], self.vendor.shop_name)
        token = browser.cookies['csrftoken'].value
        payload = {'name': 'Rider subzone', 'district': 'Matara', 'is_primary': False,
                   'coordinates': [{'lat': 5.93, 'lng': 80.57}, {'lat': 5.93, 'lng': 80.59}, {'lat': 5.95, 'lng': 80.58}]}
        created = browser.post('/api/orders/delivery-zones/', payload, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['name'], 'Rider subzone')
        self.assertEqual(browser.get('/api/orders/admin/daily-assignment/map/').status_code, 200)

    def test_invalid_primary_zone_does_not_clear_the_existing_service_boundary(self):
        self.client.force_authenticate(CustomUser.objects.create_user('0774500005', role='admin'))
        from .models import DeliveryZone
        zone_id = DeliveryZone.objects.get(is_primary=True).pk
        response = self.client.post('/api/orders/delivery-zones/', {
            'name': '', 'is_primary': True,
            'coordinates': [{'lat': 5.93, 'lng': 80.57}, {'lat': 5.93, 'lng': 80.59}, {'lat': 5.95, 'lng': 80.58}],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(DeliveryZone.objects.get(pk=zone_id).is_primary)

    def test_deleted_meals_remain_readable_and_null_meals_cannot_be_ordered(self):
        order = self.make_order()
        from .models import OrderItem
        OrderItem.objects.create(order=order, product=None, quantity=1, price_at_purchase='300.00')
        self.client.force_authenticate(self.customer)
        response = self.client.get(f'/api/orders/{order.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['items'][0]['product_name'], 'Deleted Product')
        with patch('orders.views.timezone.now', return_value=ORDER_TIME):
            response = self.client.post('/api/orders/create/', {
                'vendor': self.vendor.pk, 'location': self.location.pk, 'items': [{'product': None, 'quantity': 1}],
            }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 1)
