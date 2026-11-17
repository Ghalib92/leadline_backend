from django.apps import AppConfig


class OutreachConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.outreach"
    label = "outreach"
    verbose_name = "Leads, outreach and deadlines"

    def ready(self):
        from . import checks, receivers  # noqa: F401 — registers checks and signal receivers
