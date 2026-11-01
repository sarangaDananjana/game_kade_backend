from django.apps import AppConfig


class RidersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'riders'
    verbose_name = 'Riders'

    def ready(self):
        from . import signals  # noqa: F401
