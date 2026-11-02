from django.db import migrations, models
import orders.services.identities


def constrain_names(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    if schema_editor.connection.vendor == 'sqlite':
        # Preserve inherited index names: rebuilding this adopted table makes
        # historical schema rollback collide with the former single-shop tables.
        schema_editor.execute('CREATE UNIQUE INDEX mealdrop_order_identity_unique ON orders_order (unique_identity)')
        for event in ['INSERT', 'UPDATE']:
            schema_editor.execute(f"CREATE TRIGGER mealdrop_order_identity_{event.lower()} BEFORE {event} ON orders_order WHEN NEW.unique_identity IS NULL BEGIN SELECT RAISE(ABORT, 'Order name is required'); END")
    else:
        old = Order._meta.get_field('unique_identity')
        new = models.CharField(max_length=100, unique=True, editable=False)
        new.set_attributes_from_name('unique_identity')
        schema_editor.alter_field(Order, old, new)


def relax_names(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    if schema_editor.connection.vendor == 'sqlite':
        schema_editor.execute('DROP INDEX mealdrop_order_identity_unique')
        for event in ['insert', 'update']:
            schema_editor.execute(f'DROP TRIGGER mealdrop_order_identity_{event}')
    else:
        old = models.CharField(max_length=100, unique=True, editable=False)
        old.set_attributes_from_name('unique_identity')
        new = Order._meta.get_field('unique_identity')
        schema_editor.alter_field(Order, old, new)


def name_existing_orders(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    used = set()
    for order in Order.objects.using(schema_editor.connection.alias).only('pk').iterator():
        for _ in range(1000):
            name = orders.services.identities.generate_order_identity()
            if name not in used:
                used.add(name)
                Order.objects.using(schema_editor.connection.alias).filter(pk=order.pk).update(unique_identity=name)
                break
        else:
            raise RuntimeError('Order-name vocabulary is exhausted; expand it before migrating.')
    Location = apps.get_model('orders', 'OrderLocation')
    used = set(Location.objects.using(schema_editor.connection.alias).values_list('unique_identity', flat=True))
    for location in Location.objects.using(schema_editor.connection.alias).filter(is_system_defined=False).iterator():
        if len((location.unique_identity or '').split()) != 2:
            for _ in range(1000):
                name = orders.services.identities.generate_order_identity()
                if name not in used:
                    used.add(name)
                    Location.objects.using(schema_editor.connection.alias).filter(pk=location.pk).update(unique_identity=name)
                    break
            else:
                raise RuntimeError('Location-name vocabulary is exhausted; expand it before migrating.')


class Migration(migrations.Migration):
    dependencies = [('orders', '0006_canonical_tables_and_permissions')]
    operations = [
        migrations.AddField(model_name='order', name='unique_identity', field=models.CharField(max_length=100, null=True, editable=False)),
        migrations.RunPython(name_existing_orders, migrations.RunPython.noop),
        migrations.RunPython(constrain_names, relax_names),
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.AlterField(model_name='order', name='unique_identity', field=models.CharField(max_length=100, unique=True, editable=False,
                default=orders.services.identities.generate_order_identity, help_text='A two-word order name. This is not a delivery verification code.')),
        ]),
    ]
