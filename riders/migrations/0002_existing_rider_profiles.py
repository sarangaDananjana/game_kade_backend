from django.conf import settings
from django.db import migrations


def create_existing_rider_profiles(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    RiderInfo = apps.get_model('riders', 'RiderInfo')
    alias = schema_editor.connection.alias
    existing = set(RiderInfo.objects.using(alias).values_list('user_id', flat=True))
    RiderInfo.objects.using(alias).bulk_create([
        RiderInfo(user_id=rider_id)
        for rider_id in User.objects.using(alias).filter(role='rider').values_list('pk', flat=True)
        if rider_id not in existing
    ], ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ('riders', '0001_initial'),
        ('users', '0003_ensure_user_schema'),
    ]
    operations = [migrations.RunPython(create_existing_rider_profiles, migrations.RunPython.noop)]
