import importlib
from datetime import datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.gis.geos import Polygon
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .models import DeliveryZone
from .services.delivery_codes import delivery_code_prefix
from .test_fixtures import ORDER_TIME


class ExistingSchemaCompatibilityTests(TransactionTestCase):
    def test_compatibility_migrations_preserve_already_provisioned_schema_and_data(self):
        before = [('multivender', '0006_minimum_delivery_charge_per_parcel'),
                  ('orders', '0003_deliveryzone_district'), ('users', '0003_ensure_user_schema'),
                  ('products', '0001_initial')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            historical = executor.loader.project_state(before).apps
            User = historical.get_model('users', 'CustomUser')
            Vendor = historical.get_model('multivender', 'Vendor')
            owner = User.objects.create(phone_number='0772500001', role='owner')
            vendor = Vendor.objects.create(owner=owner, shop_name='Existing kitchen', district='Matara', lat=5.94, lng=80.58)
            editor = SimpleNamespace(connection=connection, add_field=Mock(), create_model=Mock(), alter_field=Mock())
            for module, function in [
                ('users.migrations.0003_ensure_user_schema', 'ensure_user_schema'),
                ('orders.migrations.0002_complete_delivery_schema', 'ensure_delivery_schema'),
                ('multivender.migrations.0005_complete_location_schema', 'ensure_location_schema'),
            ]:
                getattr(importlib.import_module(module), function)(historical, editor)
            editor.add_field.assert_not_called()
            editor.create_model.assert_not_called()
            editor.alter_field.assert_not_called()
            vendor.refresh_from_db()
            self.assertEqual(vendor.district, 'Matara')
        finally:
            MigrationExecutor(connection).migrate(latest)


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
