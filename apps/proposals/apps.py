from django.apps import AppConfig


class ProposalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.proposals"
    label = "proposals"

    def ready(self):
        from . import checks, receivers  # noqa: F401 — registers checks and signal receivers
