from django.apps import AppConfig


class DiscoveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.discovery"
    label = "discovery"

    def ready(self):
        from . import checks  # noqa: F401 — registers system checks
