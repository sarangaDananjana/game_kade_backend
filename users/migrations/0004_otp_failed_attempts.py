from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0003_ensure_user_schema')]

    operations = [
        migrations.AddField(
            model_name='otp',
            name='failed_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
