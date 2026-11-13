from django.apps import AppConfig


class LearningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.learning"
    label = "learning"
    verbose_name = "Learning loop and evals"

    def ready(self):
        from . import receivers  # noqa: F401 — registers signal receivers
