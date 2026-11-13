from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        # Email is USERNAME_FIELD and lookups are exact, so whitespace or case
        # would otherwise make an account unreachable at login — or let the same
        # mailbox register twice. Lowercased whole: no real provider treats the
        # local part as case-sensitive.
        return (email or "").strip().lower()

    def get_by_natural_key(self, username):
        return self.get(**{self.model.USERNAME_FIELD: self.normalize_email(username)})

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("User must have an email address")

        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self._create_user(email, password, **extra_fields)
