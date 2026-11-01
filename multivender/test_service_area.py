from unittest.mock import patch

from django.contrib.gis.geos import Polygon
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from orders.models import DeliveryZone
from orders.service_area import OUTSIDE_DELIVERY_MESSAGE, DeliveryAreaError, check_delivery_area
from .models import Order, OrderLocation
from .services.pricing import quote_delivery
from .test_delivery_pricing import PricingFixtures
from .tests import ORDER_TIME


class DeliveryAreaApiTests(PricingFixtures):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.customer)
        self.zone = DeliveryZone.objects.get(is_primary=True)
        # Narrow synthetic polygon with points on both sides of the service boundary.
        self.zone.polygon = Polygon(((80.580, 5.937), (80.582, 5.937), (80.582, 5.939), (80.580, 5.939), (80.580, 5.937)), srid=4326)
        self.zone.save()
        self.outside = OrderLocation.objects.create(user=self.customer, name='Outside entrance',
            lat=5.940, lng=80.581, district='Matara', unique_identity='Outside gate')

    def address_payload(self, **changes):
        data = {'name': 'My entrance', 'lat': self.location.lat, 'lng': self.location.lng,
                'unique_identity': 'Blue gate', 'description': 'Meet at the gate'}
        data.update(changes)
        return data

    def basket_payload(self, **location):
        return {'orders': [{'vendor': self.vendor.pk, 'items': [{'product': self.product.pk, 'quantity': 1}]}], **location}

    def test_map_api_exposes_matara_without_changing_coordinates(self):
        response = self.client.get('/api/orders/delivery-zones/')
        self.assertEqual(response.status_code, 200)
        zone = next(item for item in response.data if item['id'] == self.zone.pk)
        self.assertEqual(zone['district'], 'Matara')
        self.assertEqual(zone['coordinates'][0], {'lat': 5.937, 'lng': 80.580})

    def test_inside_address_is_saved_with_district_from_polygon(self):
        response = self.client.post('/shop/locations/', self.address_payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['district'], 'Matara')
        self.assertTrue(response.data['delivery_available'])
        self.assertIsNone(response.data['delivery_message'])

    def test_outside_address_cannot_be_added_even_with_matara_label(self):
        count = OrderLocation.objects.count()
        response = self.client.post('/shop/locations/', self.address_payload(lat=self.outside.lat, lng=self.outside.lng, district='Matara'), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['detail'], [OUTSIDE_DELIVERY_MESSAGE])
        self.assertEqual(response.data['code'], ['out_of_delivery_area'])
        self.assertEqual(OrderLocation.objects.count(), count)

    def test_updating_coordinates_outside_is_rejected_without_moving_saved_pin(self):
        response = self.client.patch(f'/shop/locations/{self.location.pk}/', {'lat': self.outside.lat, 'lng': self.outside.lng}, format='json')
        self.assertEqual(response.status_code, 400)
        self.location.refresh_from_db()
        self.assertNotEqual(self.location.lat, self.outside.lat)

    def test_partial_details_update_still_checks_existing_outside_pin(self):
        response = self.client.patch(f'/shop/locations/{self.outside.pk}/', {'name': 'Renamed'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], [OUTSIDE_DELIVERY_MESSAGE])

    def test_existing_outside_address_is_flagged_but_kept_for_the_user(self):
        response = self.client.get('/shop/locations/')
        outside = next(item for item in response.data if item['id'] == self.outside.pk)
        self.assertFalse(outside['delivery_available'])
        self.assertEqual(outside['delivery_message'], OUTSIDE_DELIVERY_MESSAGE)

    def test_outside_quotes_are_rejected_for_coordinates_and_saved_addresses(self):
        for location in [{'location': self.outside.pk}, {'lat': self.outside.lat, 'lng': self.outside.lng}]:
            with self.subTest(location=location):
                response = self.client.post('/shop/orders/delivery-quote/', self.basket_payload(**location), format='json')
                self.assertEqual(response.status_code, 422, response.data)
                self.assertEqual(response.data['code'], 'out_of_delivery_area')
                self.assertEqual(response.data['detail'], OUTSIDE_DELIVERY_MESSAGE)

    def test_client_coordinate_hints_cannot_override_saved_outside_address(self):
        response = self.client.post('/shop/orders/delivery-quote/', self.basket_payload(location=self.outside.pk, lat=self.location.lat, lng=self.location.lng), format='json')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data['code'], 'out_of_delivery_area')

    def test_inside_quote_and_checkout_continue_to_agree(self):
        quote = self.client.post('/shop/orders/delivery-quote/', self.basket_payload(location=self.location.pk), format='json')
        self.assertEqual(quote.status_code, 200, quote.data)
        with patch('multivender.views.timezone.now', return_value=ORDER_TIME):
            response = self.client.post('/shop/orders/create/', {'vendor': self.vendor.pk, 'location': self.location.pk,
                'items': [{'product': self.product.pk, 'quantity': 1}], 'expected_total_amount': quote.data['total_amount']}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total_amount'], quote.data['total_amount'])

    def test_outside_checkout_is_rejected_and_creates_no_order(self):
        with patch('multivender.views.timezone.now', return_value=ORDER_TIME):
            response = self.client.post('/shop/orders/create/', {'vendor': self.vendor.pk, 'location': self.outside.pk,
                'items': [{'product': self.product.pk, 'quantity': 1}]}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['location'], OUTSIDE_DELIVERY_MESSAGE)
        self.assertEqual(Order.objects.count(), 0)

    def test_missing_or_inactive_primary_zone_never_permits_delivery(self):
        self.zone.is_active = False
        self.zone.save()
        # An active rider subzone cannot replace the customer-facing primary boundary.
        DeliveryZone.objects.create(name='Rider zone', polygon=self.zone.polygon)
        response = self.client.post('/shop/locations/', self.address_payload(), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], ['delivery_area_unavailable'])
        response = self.client.post('/shop/orders/delivery-quote/', self.basket_payload(location=self.location.pk), format='json')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data['code'], 'delivery_area_unavailable')

    def test_boundary_is_included_and_a_polygon_hole_is_excluded(self):
        self.assertEqual(check_delivery_area(5.937, 80.581).pk, self.zone.pk)
        self.zone.polygon = Polygon(self.zone.polygon.coords[0], ((80.5805, 5.9375), (80.5815, 5.9375), (80.5815, 5.9385), (80.5805, 5.9385), (80.5805, 5.9375)), srid=4326)
        self.zone.save()
        with self.assertRaises(DeliveryAreaError) as failure:
            check_delivery_area(5.938, 80.581)
        self.assertEqual(failure.exception.code, 'out_of_delivery_area')

    def test_invalid_coordinates_are_rejected(self):
        for lat, lng in [(float('nan'), 80.581), (5.938, float('inf')), (91, 80.581), (None, None)]:
            with self.subTest(lat=lat, lng=lng), self.assertRaises(DeliveryAreaError) as failure:
                check_delivery_area(lat, lng)
            self.assertEqual(failure.exception.code, 'invalid_delivery_location')

    def test_pickup_remains_available_outside_delivery_area(self):
        self.vendor.is_pickup_only = True
        self.vendor.save()
        self.zone.delete()
        self.assertEqual(quote_delivery(self.vendor, self.outside, 200, 1)['delivery_fee'], '0.00')

    def test_legacy_location_endpoint_also_rejects_outside(self):
        response = self.client.post('/api/orders/locations/', self.address_payload(lat=self.outside.lat, lng=self.outside.lng), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['detail'], [OUTSIDE_DELIVERY_MESSAGE])


class DeliveryZoneDistrictMigrationTests(TransactionTestCase):
    def test_existing_polygon_and_primary_flag_are_preserved(self):
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([('orders', '0002_complete_delivery_schema')])
            old_apps = executor.loader.project_state([('orders', '0002_complete_delivery_schema')]).apps
            OldZone = old_apps.get_model('orders', 'DeliveryZone')
            polygon = Polygon(((80.5, 5.9), (80.6, 5.9), (80.6, 6.0), (80.5, 5.9)), srid=4326)
            old = OldZone.objects.create(name='Existing boundary', polygon=polygon, is_primary=True)
            executor = MigrationExecutor(connection)
            executor.migrate([('orders', '0003_deliveryzone_district')])
            zone = DeliveryZone.objects.get(pk=old.pk)
            self.assertEqual(zone.district, 'Matara')
            self.assertEqual(zone.polygon, polygon)
            self.assertTrue(zone.is_primary)
        finally:
            MigrationExecutor(connection).migrate(latest)
