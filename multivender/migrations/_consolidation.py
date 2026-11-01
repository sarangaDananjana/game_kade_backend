"""Versioned helpers for moving Meal Drop into shared domain apps.

The old single-shop catalog and orders are deliberately discarded. Current
multi-vendor records keep their IDs; saved addresses, users, and map zones remain.
"""

from math import isfinite

import h3
from django.conf import settings
from django.contrib.gis.geos import Point


def inspect_legacy_data(connection):
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        legacy_counts, current_counts = {}, {}
        for label, table, current_column in [
            ('products', 'products_product', 'vendor_id'),
            ('orders', 'orders_order', 'vendor_id'),
            ('locations', 'orders_orderlocation', 'h3_index'),
            ('items', 'orders_orderitem', None),
        ]:
            legacy_counts[label] = 0
            old_table = table in tables
            if old_table and current_column:
                columns = {column.name for column in connection.introspection.get_table_description(cursor, table)}
                old_table = current_column not in columns
            if label == 'items':
                old_table = old_table and legacy_counts['orders'] > 0
            if old_table:
                cursor.execute(f'SELECT COUNT(*) FROM {quote(table)}')
                legacy_counts[label] = cursor.fetchone()[0]
            current_table = f'multivender_{table.split("_", 1)[1]}'
            if current_table not in tables:
                current_table = table if table in tables and not old_table else None
            current_counts[label] = 0
            if current_table:
                cursor.execute(f'SELECT COUNT(*) FROM {quote(current_table)}')
                current_counts[label] = cursor.fetchone()[0]
        vendor_table = next((name for name in ['vendors_vendor', 'multivender_vendor'] if name in tables), None)
        vendors = []
        if vendor_table:
            cursor.execute(f'SELECT id, shop_name, is_active FROM {quote(vendor_table)} ORDER BY id')
            vendors = [{'id': row[0], 'shop_name': row[1], 'is_active': row[2]} for row in cursor.fetchall()]
    return {'legacy_counts': legacy_counts, 'current_multivendor_counts': current_counts, 'vendors': vendors}


def consolidate_locations_and_discard_old_logs(apps, schema_editor):
    alias = schema_editor.connection.alias
    OldLocation = apps.get_model('orders', 'LegacyOrderLocation')
    Location = apps.get_model('orders', 'OrderLocation')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    LogEntry = apps.get_model('admin', 'LogEntry')
    location_ids = {}
    for old in OldLocation.objects.using(alias).order_by('pk').iterator():
        identity = {name: getattr(old, name) for name in [
            'user_id', 'name', 'lat', 'lng', 'description', 'unique_identity', 'is_system_defined',
        ]}
        existing = Location.objects.using(alias).filter(**identity).order_by('pk').first()
        if existing:
            location_ids[str(old.pk)] = str(existing.pk)
            continue
        point = old.location_point
        valid_coordinates = (isfinite(old.lat) and isfinite(old.lng)
                             and -90 <= old.lat <= 90 and -180 <= old.lng <= 180)
        if point is None and valid_coordinates:
            point = Point(old.lng, old.lat, srid=4326)
        try:
            cell = h3.latlng_to_cell(old.lat, old.lng, 8) if valid_coordinates else None
        except (h3.H3BaseException, TypeError, ValueError):
            cell = None
        new = Location.objects.using(alias).create(district='Matara', h3_index=cell, location_point=point, **identity)
        location_ids[str(old.pk)] = str(new.pk)

    # These audit entries referred to the discarded single-shop records. Current
    # multi-vendor entries have separate content types and are retained below.
    discarded_types = ContentType.objects.using(alias).filter(
        app_label='products', model='product',
    ) | ContentType.objects.using(alias).filter(app_label='orders', model__in=['order', 'orderitem'])
    LogEntry.objects.using(alias).filter(content_type__in=discarded_types).delete()
    location_type = ContentType.objects.using(alias).filter(app_label='orders', model='orderlocation').first()
    if location_type:
        logs = LogEntry.objects.using(alias).filter(content_type=location_type)
        logs.exclude(object_id__in=location_ids).delete()
        # Snapshot IDs first so overlapping old and new IDs cannot chain updates.
        entries = list(logs.values_list('pk', 'object_id'))
        for entry_id, object_id in entries:
            LogEntry.objects.using(alias).filter(pk=entry_id).update(object_id=location_ids[object_id])


def transfer_domain_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    LogEntry = apps.get_model('admin', 'LogEntry')
    for model_name, app_label in [
        ('vendor', 'vendors'), ('product', 'products'), ('order', 'orders'),
        ('orderitem', 'orders'), ('orderlocation', 'orders'), ('deliverydistancetier', 'orders'),
    ]:
        source = ContentType.objects.using(alias).filter(app_label='multivender', model=model_name).first()
        if source is None:
            continue
        target, _ = ContentType.objects.using(alias).get_or_create(app_label=app_label, model=model_name)
        for permission in Permission.objects.using(alias).filter(content_type=source):
            copied, _ = Permission.objects.using(alias).get_or_create(
                content_type=target, codename=permission.codename, defaults={'name': permission.name},
            )
            for Model, field_name in [(User, 'user_permissions'), (Group, 'permissions')]:
                Through = Model._meta.get_field(field_name).remote_field.through
                owner_field = next(field for field in Through._meta.fields
                                   if field.is_relation and field.remote_field.model == Model)
                owner_ids = Through.objects.using(alias).filter(permission_id=permission.pk).values_list(owner_field.attname, flat=True)
                for owner_id in owner_ids:
                    Through.objects.using(alias).get_or_create(**{owner_field.attname: owner_id, 'permission_id': copied.pk})
        LogEntry.objects.using(alias).filter(content_type=source).update(content_type=target)

    # There are no live models in the compatibility app. Grants and valid audit
    # references now point at the shared domains; unused placeholder metadata goes.
    stale_types = ContentType.objects.using(alias).filter(app_label='multivender')
    LogEntry.objects.using(alias).filter(content_type__in=stale_types).delete()
    stale_types.delete()
