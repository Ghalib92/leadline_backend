import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from shortuuid.django_fields import ShortUUIDField

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = ShortUUIDField(length=12, max_length=12,
                        primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, max_length=255)
    is_staff = models.BooleanField(default=False)
    # "Email confirmed" — login is refused until this is set.
    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    # One live code per user, shared by email verification and password reset:
    # both only prove the user can read this mailbox.
    otp_code = models.CharField(max_length=6, blank=True, default="")
    otp_expiry = models.DateTimeField(blank=True, null=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.__class__.objects.normalize_email(self.email)
        super().save(*args, **kwargs)

    def get_full_name(self):
        profile = getattr(self, "profile", None)
        if profile:
            full_name = f"{profile.first_name} {profile.last_name}".strip()
            if full_name:
                return full_name
        return self.email

    def get_short_name(self):
        profile = getattr(self, "profile", None)
        return (profile.first_name if profile else None) or self.email


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    first_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profiles"
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self):
        name = f"{self.first_name} {self.last_name}".strip()
        return name or f"Profile for {self.user.email}"
