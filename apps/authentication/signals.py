from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User, UserProfile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Every user has a profile row, however they were created."""
    if created:
        UserProfile.objects.get_or_create(user=instance)
