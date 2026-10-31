"""Complete the state-only legacy migration on new database installations."""

from django.db import migrations


def ensure_user_schema(apps, schema_editor):
    User = apps.get_model('users', 'CustomUser')
    AppVersion = apps.get_model('users', 'AppVersion')
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        columns = {column.name for column in connection.introspection.get_table_description(cursor, User._meta.db_table)}
        tables = set(connection.introspection.table_names(cursor))
    if 'role' not in columns:
        schema_editor.add_field(User, User._meta.get_field('role'))
    if AppVersion._meta.db_table not in tables:
        schema_editor.create_model(AppVersion)


class Migration(migrations.Migration):
    dependencies = [('users', '0002_customuser_role_appversion')]
    operations = [migrations.RunPython(ensure_user_schema, migrations.RunPython.noop)]
