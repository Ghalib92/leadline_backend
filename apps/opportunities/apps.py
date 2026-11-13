from django.apps import AppConfig


class OpportunitiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.opportunities"
    label = "opportunities"

    def ready(self):
        from . import receivers  # noqa: F401 — registers signal receivers
