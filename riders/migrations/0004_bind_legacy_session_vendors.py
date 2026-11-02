from django.db import migrations


def bind_vendors(apps, schema_editor):
    Session = apps.get_model('riders', 'Session')
    SessionOrder = apps.get_model('riders', 'SessionOrder')
    alias = schema_editor.connection.alias
    for session in Session.objects.using(alias).filter(vendor=None).iterator():
        vendors = list(SessionOrder.objects.using(alias).filter(session=session, order__isnull=False)
                       .values_list('order__vendor_id', flat=True).distinct())
        if len(vendors) == 1:
            Session.objects.using(alias).filter(pk=session.pk).update(vendor_id=vendors[0])


class Migration(migrations.Migration):
    dependencies = [('riders', '0003_joboffer_ridercancellation_riderpenalty_and_more')]
    operations = [migrations.RunPython(bind_vendors, migrations.RunPython.noop)]
