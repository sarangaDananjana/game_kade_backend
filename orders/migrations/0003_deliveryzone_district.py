from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0002_complete_delivery_schema')]

    operations = [
        migrations.AddField(
            model_name='deliveryzone',
            name='district',
            field=models.CharField(choices=[('Matara', 'Matara')], default='Matara', max_length=50),
        ),
    ]
