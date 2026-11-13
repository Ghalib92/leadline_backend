from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.organizations'
    label = 'organizations'

    def ready(self):
        from . import checks  # noqa: F401 — registers system checks
