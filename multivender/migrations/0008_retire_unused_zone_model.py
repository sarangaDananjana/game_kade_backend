"""Remove the unused name-only zone model; orders.DeliveryZone stays authoritative."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('multivender', '0007_release_domain_models'),
        ('orders', '0005_consolidate_locations_remove_single_shop_orders'),
    ]
    operations = [migrations.DeleteModel(name='DeliveryZone')]
