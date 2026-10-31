"""Fill missing location columns while preserving manually provisioned schemas."""

import django.db.models.deletion
from django.db import migrations, models


def ensure_location_schema(apps, schema_editor):
    connection = schema_editor.connection
    for model_name in ['Vendor', 'OrderLocation']:
        Model = apps.get_model('multivender', model_name)
        with connection.cursor() as cursor:
            columns = {column.name for column in connection.introspection.get_table_description(cursor, Model._meta.db_table)}
        if 'district' not in columns:
            schema_editor.add_field(Model, Model._meta.get_field('district'))

    Order = apps.get_model('multivender', 'Order')
    Zone = apps.get_model('orders', 'DeliveryZone')
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, Order._meta.db_table)
    targets = [constraint['foreign_key'][0] for constraint in constraints.values()
               if constraint.get('foreign_key') and constraint['columns'] == ['zone_id']]
    if targets == [Zone._meta.db_table]:
        return
    assigned = set(Order.objects.using(connection.alias).exclude(zone_id=None).values_list('zone_id', flat=True))
    valid = set(Zone.objects.using(connection.alias).filter(pk__in=assigned).values_list('pk', flat=True))
    if assigned - valid:
        raise RuntimeError('Existing multi-vendor zone IDs need to be mapped to orders.DeliveryZone before migrating.')
    field = Order._meta.get_field('zone')
    old_field = field.clone()
    old_field.set_attributes_from_name('zone')
    old_field.model = Order
    old_field.remote_field.model = apps.get_model('multivender', 'DeliveryZone')
    schema_editor.alter_field(Order, old_field, field)


class Migration(migrations.Migration):
    dependencies = [('multivender', '0004_numeric_delivery_codes'), ('orders', '0002_complete_delivery_schema')]
    operations = [
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.AddField(model_name='orderlocation', name='district', field=models.CharField(blank=True, choices=[('Matara', 'Matara')], max_length=50, null=True)),
            migrations.AddField(model_name='vendor', name='district', field=models.CharField(blank=True, choices=[('Matara', 'Matara')], max_length=50, null=True)),
            migrations.AlterField(model_name='order', name='zone', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='multivendor_orders', to='orders.deliveryzone')),
            migrations.AlterField(model_name='orderlocation', name='h3_index', field=models.CharField(blank=True, help_text='H3 spatial index (Resolution 8)', max_length=15, null=True)),
            migrations.AlterField(model_name='vendor', name='h3_index', field=models.CharField(blank=True, help_text='H3 spatial index (Resolution 8)', max_length=15, null=True)),
        ]),
        migrations.RunPython(ensure_location_schema, migrations.RunPython.noop),
    ]
