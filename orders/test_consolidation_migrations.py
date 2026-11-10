from decimal import Decimal

import h3
from django.apps import apps
from django.contrib.gis.geos import Point, Polygon
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from products.models import Product
from vendors.models import Vendor
from .models import DeliveryDistanceTier, DeliveryZone, Order, OrderItem, OrderLocation
from .test_fixtures import ORDER_TIME
from multivender.migrations._consolidation import inspect_legacy_data


class ConsolidationMigrationTests(TransactionTestCase):
    before = [
        ('vendors', None),
        ('users', '0003_ensure_user_schema'), ('products', '0001_initial'),
        ('orders', '0003_deliveryzone_district'), ('multivender', '0006_minimum_delivery_charge_per_parcel'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        executor.migrate(self.before)
        self.historical = executor.loader.project_state([target for target in self.before if target[1] is not None]).apps
        User = self.historical.get_model('users', 'CustomUser')
        self.customer = User.objects.create(phone_number='0773500001', name='Customer')
        self.owner = User.objects.create(phone_number='0773500002', role='owner')
        self.rider = User.objects.create(phone_number='0773500003', role='rider')
        self.vendor = self.historical.get_model('multivender', 'Vendor').objects.create(
            pk=11, owner=self.owner, shop_name='Original kitchen', lat=5.94, lng=80.58,
            logo='vendor_logos/original.png', cover_image='vendor_covers/original.jpg',
            district='Matara', h3_index=h3.latlng_to_cell(5.94, 80.58, 8),
            location_point=Point(80.58, 5.94, srid=4326),
        )
        self.polygon = Polygon(((80.3, 5.7), (80.9, 5.7), (80.9, 6.3), (80.3, 6.3), (80.3, 5.7)), srid=4326)
        self.zone = self.historical.get_model('orders', 'DeliveryZone').objects.create(
            pk=1, name='Existing service boundary', district='Matara', polygon=self.polygon, is_primary=True,
        )
        self.historical.get_model('multivender', 'DeliveryZone').objects.create(pk=1, name='Unused zone label')
        self.historical.get_model('multivender', 'DeliveryDistanceTier').objects.create(
            pk=1, grid_distance=0, delivery_fee='50.00', minimum_delivery_charge_per_parcel='10.00',
        )
        self.current_product = self.historical.get_model('multivender', 'Product').objects.create(
            pk=1, vendor=self.vendor, name='Current meal', price='320.00', stock_quantity=23,
            image='product_images/current.jpg',
        )
        self.current_location = self.historical.get_model('multivender', 'OrderLocation').objects.create(
            pk=1, user=self.customer, name='Current address', lat=5.94, lng=80.58,
            unique_identity='Green gate', district='Matara', h3_index=self.vendor.h3_index,
            location_point=Point(80.58, 5.94, srid=4326),
        )
        self.current_order = self.historical.get_model('multivender', 'Order').objects.create(
            pk=1, user=self.customer, vendor=self.vendor, location=self.current_location, zone=self.zone,
            rider=self.rider, status='preparing', total_amount='400.00', delivery_fee='80.00',
            delivery_code='2611010042', created_at=ORDER_TIME,
        )
        self.historical.get_model('multivender', 'OrderItem').objects.create(
            pk=1, order=self.current_order, product=self.current_product, quantity=1, price_at_purchase='320.00',
        )
        OldProduct = self.historical.get_model('products', 'Product')
        self.old_product = OldProduct.objects.create(
            pk=1, name='Old meal', category='rice_curry', price='310.00', stock_quantity=7,
            image='product_images/old.jpg', description='Original menu',
        )
        self.old_second_product = OldProduct.objects.create(pk=2, name='Old drink', price='90.00', stock_quantity=12)
        OldProduct.objects.filter(pk__in=[1, 2]).update(created_at=ORDER_TIME)
        self.old_location = self.historical.get_model('orders', 'OrderLocation').objects.create(
            pk=1, name='Old shared address', lat=5.95, lng=80.59, unique_identity='Blue gate',
            is_system_defined=True, description='Original entrance', location_point=Point(80.59, 5.95, srid=4326),
        )
        OldOrder = self.historical.get_model('orders', 'Order')
        self.old_order = OldOrder.objects.create(
            pk=1, user=self.customer, rider=self.rider, zone=self.zone, location=self.old_location,
            status='out_for_delivery', total_amount='999.00',
        )
        OldOrder.objects.filter(pk=1).update(created_at=ORDER_TIME)
        OldItem = self.historical.get_model('orders', 'OrderItem')
        OldItem.objects.create(pk=1, order=self.old_order, product=self.old_product, quantity=2, price_at_purchase='300.00')
        OldItem.objects.create(pk=2, order=self.old_order, product=None, quantity=1, price_at_purchase='120.00')

        ContentType = self.historical.get_model('contenttypes', 'ContentType')
        Permission = self.historical.get_model('auth', 'Permission')
        Group = self.historical.get_model('auth', 'Group')
        content_type, _ = ContentType.objects.get_or_create(app_label='multivender', model='product')
        self.permission, _ = Permission.objects.get_or_create(
            content_type=content_type, codename='change_product', defaults={'name': 'Can change product'},
        )
        self.owner.user_permissions.add(self.permission)
        self.group = Group.objects.create(name='Kitchen operators')
        self.group.permissions.add(self.permission)
        self.owner.groups.add(self.group)
        old_content_type, _ = ContentType.objects.get_or_create(app_label='products', model='product')
        LogEntry = self.historical.get_model('admin', 'LogEntry')
        self.log_ids = [LogEntry.objects.create(
            user=self.owner, content_type=old_content_type, object_id=str(pk), object_repr=name, action_flag=2,
        ).pk for pk, name in [(1, 'Old meal'), (2, 'Old drink')]]
        self.current_log_id = LogEntry.objects.create(
            user=self.owner, content_type=content_type, object_id='1', object_repr='Current meal', action_flag=2,
        ).pk

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.latest)
        super().tearDown()

    def migrate_latest(self):
        MigrationExecutor(connection).migrate(self.latest)

    def test_old_catalog_orders_and_items_are_deleted_and_current_records_keep_ids(self):
        self.migrate_latest()
        self.assertEqual(Vendor.objects.get(pk=11).logo.name, 'vendor_logos/original.png')
        product = Product.objects.get(pk=1)
        self.assertEqual(product.name, 'Current meal')
        self.assertEqual(product.stock_quantity, 23)
        self.assertEqual(product.image.name, 'product_images/current.jpg')
        self.assertEqual(Product.objects.count(), 1)
        self.assertFalse(Product.objects.filter(name__in=['Old meal', 'Old drink']).exists())
        order = Order.objects.get(pk=1)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(order.vendor_id, 11)
        self.assertEqual(order.user_id, self.customer.pk)
        self.assertEqual(order.rider_id, self.rider.pk)
        self.assertEqual(order.zone_id, self.zone.pk)
        self.assertEqual(order.location_id, self.current_location.pk)
        self.assertEqual(order.total_amount, Decimal('400.00'))
        self.assertEqual(order.delivery_fee, Decimal('80.00'))
        self.assertEqual(order.created_at, ORDER_TIME)
        self.assertEqual(order.status, 'preparing')
        self.assertEqual(order.delivery_code, '2611010042')
        self.assertFalse(hasattr(order, "legacy_delivery_code"))
        self.assertEqual(OrderItem.objects.count(), 1)
        self.assertEqual(OrderItem.objects.get(pk=1).product_id, 1)
        self.assertEqual(OrderItem.objects.get(pk=1).price_at_purchase, Decimal('320.00'))
        self.assertEqual(DeliveryZone.objects.get(pk=1).polygon.ewkb, self.polygon.ewkb)
        self.assertTrue(DeliveryZone.objects.get(pk=1).is_primary)
        self.assertEqual(DeliveryZone.objects.count(), 1)
        tier = DeliveryDistanceTier.objects.get(pk=1)
        self.assertEqual(tier.delivery_fee, Decimal('50.00'))
        self.assertEqual(tier.minimum_delivery_charge_per_parcel, Decimal('10.00'))
        self.assertEqual(list(apps.get_app_config('multivender').get_models()), [])
        self.assertEqual({model.__name__ for model in apps.get_app_config('orders').get_models()},
                         {'Order', 'OrderItem', 'OrderLocation', 'DeliveryZone', 'DeliveryDistanceTier', 'OrderingWindow'})
        self.assertFalse(any(name.startswith('multivender_') for name in connection.introspection.table_names()))
        from users.models import CustomUser
        from django.contrib.admin.models import LogEntry
        from django.contrib.auth.models import Group
        from django.contrib.contenttypes.models import ContentType
        self.assertTrue(CustomUser.objects.get(pk=self.owner.pk).has_perm('products.change_product'))
        self.assertTrue(Group.objects.get(pk=self.group.pk).permissions.filter(
            content_type__app_label='products', codename='change_product',
        ).exists())
        self.assertFalse(LogEntry.objects.filter(pk__in=self.log_ids).exists())
        self.assertEqual(LogEntry.objects.get(pk=self.current_log_id).content_type.app_label, 'products')
        self.assertEqual(LogEntry.objects.get(pk=self.current_log_id).object_id, '1')
        self.assertFalse(ContentType.objects.filter(app_label='multivender').exists())
        connection.check_constraints()
        new_product = Product.objects.create(vendor_id=11, name='New shared meal', price='200.00')
        self.assertGreater(new_product.pk, 1)

    def test_saved_addresses_are_retained_and_exact_duplicates_are_merged(self):
        OldLocation = self.historical.get_model('orders', 'OrderLocation')
        OldLocation.objects.create(
            user=self.customer, name='Current address', lat=5.94, lng=80.58,
            unique_identity='Green gate', description=None, is_system_defined=False,
        )
        self.migrate_latest()
        self.assertEqual(OrderLocation.objects.count(), 2)
        self.assertEqual(OrderLocation.objects.filter(name='Current address').count(), 1)
        self.assertEqual(OrderLocation.objects.get(pk=1).user_id, self.customer.pk)
        shared = OrderLocation.objects.get(name='Old shared address')
        self.assertTrue(shared.is_system_defined)
        self.assertEqual(shared.location_point, Point(80.59, 5.95, srid=4326))
        self.assertEqual(shared.h3_index, h3.latlng_to_cell(5.95, 80.59, 8))
        self.assertEqual(shared.district, 'Matara')
        self.assertEqual(shared.description, 'Original entrance')

    def test_schema_rollback_does_not_resurrect_discarded_single_shop_records(self):
        self.migrate_latest()
        MigrationExecutor(connection).migrate(self.before)
        self.assertEqual(self.historical.get_model('products', 'Product').objects.count(), 0)
        self.assertEqual(self.historical.get_model('orders', 'Order').objects.count(), 0)
        self.assertEqual(self.historical.get_model('orders', 'OrderItem').objects.count(), 0)
        self.assertEqual(self.historical.get_model('multivender', 'Product').objects.count(), 1)
        current = self.historical.get_model('multivender', 'Order').objects.get(pk=1)
        self.assertEqual(current.delivery_code, '2611010042')
        self.assertEqual(current.total_amount, Decimal('400.00'))
        self.assertEqual(self.historical.get_model('multivender', 'DeliveryZone').objects.count(), 0)
        connection.check_constraints()

    def test_legacy_uuid_overlap_is_discarded_without_touching_current_delivery_codes(self):
        self.historical.get_model('orders', 'Order').objects.filter(pk=1).update(
            delivery_code=self.current_order.legacy_delivery_code,
        )
        self.migrate_latest()
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.get(pk=1).delivery_code, '2611010042')

    def test_preflight_reports_old_and_current_record_counts_without_changing_them(self):
        before = inspect_legacy_data(connection)
        self.assertEqual(before['legacy_counts'], {'products': 2, 'orders': 1, 'locations': 1, 'items': 2})
        self.assertEqual(before['current_multivendor_counts'], {'products': 1, 'orders': 1, 'locations': 1, 'items': 1})
        self.assertEqual(self.historical.get_model('products', 'Product').objects.count(), 2)
        self.migrate_latest()
        after = inspect_legacy_data(connection)
        self.assertEqual(after['legacy_counts'], {'products': 0, 'orders': 0, 'locations': 0, 'items': 0})
        self.assertEqual(after['current_multivendor_counts'], {'products': 1, 'orders': 1, 'locations': 2, 'items': 1})
