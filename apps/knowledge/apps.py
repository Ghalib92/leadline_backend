from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.knowledge"
    label = "knowledge"
    verbose_name = "Company knowledge"

    def ready(self):
        from . import checks, receivers  # noqa: F401 — registers checks and signal receivers
