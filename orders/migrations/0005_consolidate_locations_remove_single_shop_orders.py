"""Consolidate addresses and deliberately delete the old single-shop orders."""

import multivender.migrations._consolidation
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('admin', '0003_logentry_add_action_flag_choices'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('multivender', '0007_release_domain_models'),
        ('orders', '0004_adopt_multivendor_orders'),
    ]

    operations = [
        migrations.RunPython(
            code=multivender.migrations._consolidation.consolidate_locations_and_discard_old_logs,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.DeleteModel(
            name='LegacyOrderItem',
        ),
        migrations.DeleteModel(
            name='LegacyOrder',
        ),
        migrations.DeleteModel(
            name='LegacyOrderLocation',
        ),
    ]
