"""Remove order names and UUID codes while preserving numeric PIN/QR codes."""
import uuid
from django.db import migrations, models
from django.db.migrations.state import ProjectState
from django.db.models import NOT_PROVIDED
from orders.services.identities import generate_location_identity


def sqlite_indexes(schema_editor):
    if schema_editor.connection.vendor != 'sqlite':
        return {}
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='orders_order' AND sql IS NOT NULL")
        return dict(cursor.fetchall())


def restore_indexes(schema_editor, original):
    if schema_editor.connection.vendor != 'sqlite':
        return
    # Retain adopted index names so historical schema rollback cannot collide.
    desired = {name: sql for name, sql in original.items()
               if 'unique_identity' not in sql and 'legacy_delivery_code' not in sql}
    for name in sqlite_indexes(schema_editor):
        if name not in desired:
            schema_editor.execute(f'DROP INDEX {schema_editor.quote_name(name)}')
    current = sqlite_indexes(schema_editor)
    for name, sql in desired.items():
        if name not in current:
            schema_editor.execute(sql)


def remove_fields(apps, schema_editor):
    original = sqlite_indexes(schema_editor)
    state = ProjectState.from_apps(apps)
    if schema_editor.connection.vendor == 'sqlite':
        schema_editor.execute('DROP INDEX IF EXISTS mealdrop_order_identity_unique')
        for event in ['insert', 'update']:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS mealdrop_order_identity_{event}')
        field = state.models['orders', 'order'].fields['unique_identity'].clone()
        field._unique, field.null, field.default = False, True, NOT_PROVIDED
        state.alter_field('orders', 'order', 'unique_identity', field, preserve_default=True)
    for name in ['unique_identity', 'legacy_delivery_code']:
        model = state.apps.get_model('orders', 'Order')
        schema_editor.remove_field(model, model._meta.get_field(name))
        state.remove_field('orders', 'order', name)
    restore_indexes(schema_editor, original)


def restore_fields(apps, schema_editor):
    # Rollback generates replacement UUIDs/names; discarded values cannot be recovered.
    original = sqlite_indexes(schema_editor)
    state = ProjectState.from_apps(apps)
    for name in ['unique_identity', 'legacy_delivery_code']:
        if name in state.models['orders', 'order'].fields:
            state.remove_field('orders', 'order', name)
    fields = {'unique_identity': models.CharField(max_length=100, null=True, editable=False),
              'legacy_delivery_code': models.UUIDField(null=True, editable=False)}
    for name, field in fields.items():
        field.set_attributes_from_name(name)
        model = state.apps.get_model('orders', 'Order')
        schema_editor.add_field(model, field)
        state.add_field('orders', 'order', name, field, preserve_default=True)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT id FROM orders_order')
        ids = [row[0] for row in cursor.fetchall()]
        used = set()
        for pk in ids:
            identity = generate_location_identity()
            while identity in used:
                identity = generate_location_identity()
            used.add(identity)
            legacy = uuid.uuid4()
            field = state.apps.get_model('orders', 'Order')._meta.get_field('legacy_delivery_code')
            cursor.execute('UPDATE orders_order SET unique_identity=%s, legacy_delivery_code=%s WHERE id=%s',
                [identity, field.get_db_prep_value(legacy, schema_editor.connection), pk])
    legacy = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    legacy.set_attributes_from_name('legacy_delivery_code')
    model = state.apps.get_model('orders', 'Order')
    schema_editor.alter_field(model, model._meta.get_field('legacy_delivery_code'), legacy)
    state.alter_field('orders', 'order', 'legacy_delivery_code', legacy, preserve_default=True)
    restore_indexes(schema_editor, original)
    if schema_editor.connection.vendor == 'sqlite':
        schema_editor.execute('CREATE UNIQUE INDEX mealdrop_order_identity_unique ON orders_order (unique_identity)')
        for event in ['INSERT', 'UPDATE']:
            schema_editor.execute(f"CREATE TRIGGER mealdrop_order_identity_{event.lower()} BEFORE {event} ON orders_order WHEN NEW.unique_identity IS NULL BEGIN SELECT RAISE(ABORT, 'Order name is required'); END")
    else:
        identity = models.CharField(max_length=100, unique=True, editable=False, default=generate_location_identity)
        identity.set_attributes_from_name('unique_identity')
        model = state.apps.get_model('orders', 'Order')
        schema_editor.alter_field(model, model._meta.get_field('unique_identity'), identity)


class Migration(migrations.Migration):
    dependencies = [('orders', '0007_order_unique_identity')]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunPython(remove_fields, restore_fields)],
        state_operations=[migrations.RemoveField(model_name='order', name='unique_identity'),
                          migrations.RemoveField(model_name='order', name='legacy_delivery_code')],
    )]
