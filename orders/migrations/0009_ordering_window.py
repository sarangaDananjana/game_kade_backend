import datetime

from django.db import migrations, models


def seed_ordering_window(apps, schema_editor):
    apps.get_model('orders', 'OrderingWindow').objects.using(
        schema_editor.connection.alias,
    ).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('orders', '0008_remove_order_identity_and_legacy_code')]

    operations = [
        migrations.CreateModel(
            name='OrderingWindow',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('enabled', models.BooleanField(default=True, help_text='Uncheck to temporarily stop accepting new orders.')),
                ('opens_at', models.TimeField(default=datetime.time(12, 0), help_text='Daily opening time in Asia/Colombo (Sri Lanka).')),
                ('closes_at', models.TimeField(default=datetime.time(17, 0), help_text='Orders stop at this time. An earlier closing time means the following day.')),
            ],
            options={
                'verbose_name': 'Ordering window',
                'verbose_name_plural': 'Ordering window',
                'constraints': [
                    models.CheckConstraint(condition=models.Q(id=1), name='single_ordering_window'),
                    models.CheckConstraint(condition=~models.Q(opens_at=models.F('closes_at')), name='ordering_window_distinct_times'),
                ],
            },
        ),
        migrations.RunPython(seed_ordering_window, migrations.RunPython.noop),
    ]
