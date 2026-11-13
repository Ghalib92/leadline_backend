from django.apps import AppConfig


class AiConfig(AppConfig):
    name = "apps.ai"
    label = "ai"
    verbose_name = "AI agents"

    def ready(self):
        from . import checks  # noqa: F401 — registers system checks
        from .tracing import configure_tracing

        configure_tracing()
