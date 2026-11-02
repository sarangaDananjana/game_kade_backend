from datetime import datetime, timezone as datetime_timezone

from django.contrib.gis.geos import Polygon
from django.test import TestCase

from users.models import CustomUser
from vendors.models import Vendor
from products.models import Product
from orders.models import DeliveryZone as MapDeliveryZone, Order, OrderLocation

ORDER_TIME = datetime(2026, 11, 1, 8, 35, tzinfo=datetime_timezone.utc)


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
        for rider in [cls.rider, cls.other_rider]:
            rider.name = 'Rider'
            rider.save(update_fields=['name'])
            profile = rider.rider_info
            profile.vehicle_registration, profile.vehicle_name, profile.vehicle_colour = 'BHF 2841', 'Bajaj CT 100', 'Black'
            profile.emergency_contact = '0771000000'
            profile.save()
        cls.vendor = Vendor.objects.create(owner=cls.owner, shop_name='Dinner kitchen', lat=5.938075, lng=80.581056)
        cls.location = OrderLocation.objects.create(user=cls.customer, name='Boarding', lat=5.938075, lng=80.581056, unique_identity='Blue gate')
        cls.product = Product.objects.create(vendor=cls.vendor, name='Rice and curry', price='320.00', stock_quantity=100)

    def make_order(self, **changes):
        data = dict(user=self.customer, vendor=self.vendor, location=self.location,
                    rider=self.rider, total_amount='350.00', delivery_fee='30.00',
                    status='preparing', created_at=ORDER_TIME)
        data.update(changes)
        return Order.objects.create(**data)
