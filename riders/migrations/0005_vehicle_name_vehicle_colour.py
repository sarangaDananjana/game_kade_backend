from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('riders', '0004_bind_legacy_session_vendors')]
    operations = [
        migrations.AddField(model_name='riderinfo', name='vehicle_name', field=models.CharField(blank=True, max_length=80, help_text='Make and model, e.g. Bajaj CT 100.')),
        migrations.AddField(model_name='riderinfo', name='vehicle_colour', field=models.CharField(blank=True, max_length=40)),
    ]
