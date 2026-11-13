import uuid

from django.db import models

from shortuuid.django_fields import ShortUUIDField

class UUIDTimestampedModel(models.Model):
    """Shared identity and timestamps for domain records."""

    id = ShortUUIDField(length=12, max_length=12,
                        primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
