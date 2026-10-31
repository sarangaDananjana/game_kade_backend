"""Record delivery fields that were previously added outside migration history."""

import django.contrib.gis.db.models.fields
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def ensure_delivery_schema(apps, schema_editor):
    connection = schema_editor.connection
    Zone = apps.get_model('orders', 'DeliveryZone')
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    if Zone._meta.db_table not in tables:
        schema_editor.create_model(Zone)
    for model_name, names in [('Order', ['rider', 'zone']), ('OrderLocation', ['location_point'])]:
        Model = apps.get_model('orders', model_name)
        with connection.cursor() as cursor:
            columns = {column.name for column in connection.introspection.get_table_description(cursor, Model._meta.db_table)}
        for name in names:
            field = Model._meta.get_field(name)
            if field.column not in columns:
                schema_editor.add_field(Model, field)


class Migration(migrations.Migration):
    dependencies = [('orders', '0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.CreateModel(
                name='DeliveryZone', fields=[
                    ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ('name', models.CharField(max_length=100)),
                    ('polygon', django.contrib.gis.db.models.fields.PolygonField(help_text='Draw the delivery zone on the map', srid=4326)),
                    ('is_active', models.BooleanField(default=True)),
                    ('is_primary', models.BooleanField(default=False)),
                ],
            ),
            migrations.AddField(model_name='order', name='rider', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_deliveries', to=settings.AUTH_USER_MODEL)),
            migrations.AddField(model_name='order', name='zone', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='orders.deliveryzone')),
            migrations.AddField(model_name='orderlocation', name='location_point', field=django.contrib.gis.db.models.fields.PointField(blank=True, geography=True, null=True, srid=4326)),
            migrations.AlterField(model_name='order', name='delivery_code', field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            migrations.AlterField(model_name='orderlocation', name='description', field=models.TextField(blank=True, help_text='Landmark or additional details', null=True)),
            migrations.AlterField(model_name='orderlocation', name='unique_identity', field=models.CharField(help_text='Two word sentence to identify location', max_length=100)),
        ]),
        migrations.RunPython(ensure_delivery_schema, migrations.RunPython.noop),
    ]
