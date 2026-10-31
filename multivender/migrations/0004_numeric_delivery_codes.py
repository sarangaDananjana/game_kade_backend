import random
from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo

import django.utils.timezone
from django.db import migrations, models


def assign_numeric_codes(apps, schema_editor):
    Order = apps.get_model('multivender', 'Order')
    orders = Order.objects.using(schema_editor.connection.alias)
    randomizer = random.SystemRandom()
    current_prefix = None
    available = []
    for order in orders.order_by('created_at', 'pk').iterator():
        timestamp = order.created_at
        if django.utils.timezone.is_naive(timestamp):
            timestamp = django.utils.timezone.make_aware(timestamp, datetime_timezone.utc)
        prefix = timestamp.astimezone(ZoneInfo('Asia/Colombo')).strftime('%y%m%d')
        if prefix != current_prefix:
            available = list(range(10_000))
            randomizer.shuffle(available)
            current_prefix = prefix
        if not available:
            raise RuntimeError(f'More than 10,000 existing orders use the delivery date {prefix}.')
        orders.filter(pk=order.pk).update(delivery_code=f'{prefix}{available.pop():04d}')


class Migration(migrations.Migration):
    dependencies = [('multivender', '0003_h3_spatial_pricing')]

    operations = [
        migrations.RenameField(
            model_name='order', old_name='delivery_code', new_name='legacy_delivery_code',
        ),
        migrations.AddField(
            model_name='order', name='delivery_code',
            field=models.CharField(editable=False, max_length=10, null=True, unique=True),
        ),
        migrations.RunPython(assign_numeric_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order', name='delivery_code',
            field=models.CharField(editable=False, max_length=10, unique=True),
        ),
        migrations.AlterField(
            model_name='order', name='created_at',
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False),
        ),
        migrations.AddConstraint(
            model_name='order',
            constraint=models.CheckConstraint(
                condition=models.Q(delivery_code__regex=r'^[0-9]{10}$'),
                name='multivendor_delivery_code_ten_digits',
            ),
        ),
    ]
