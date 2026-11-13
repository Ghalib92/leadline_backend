from django.db import models


class DossierStatus(models.TextChoices):
    # Not generated yet, or the organization has too little context to summarise.
    PENDING = "pending", "Pending"
    GENERATING = "generating", "Generating"
    READY = "ready", "Ready"
    # The last attempt failed; any earlier content is still served.
    FAILED = "failed", "Failed"
