from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('multivender', '0005_complete_location_schema')]
    operations = [
        migrations.AddField(
            model_name='deliverydistancetier', name='minimum_delivery_charge_per_parcel',
            field=models.DecimalField(
                blank=True, null=True, max_digits=10, decimal_places=2,
                validators=[django.core.validators.MinValueValidator(Decimal('0.00'))],
                help_text='Minimum delivery charge for each individual meal delivered to this tier. Set this before enabling delivery quotes.',
            ),
        ),
        migrations.AlterField(
            model_name='deliverydistancetier', name='delivery_fee',
            field=models.DecimalField(max_digits=10, decimal_places=2, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))]),
        ),
    ]
