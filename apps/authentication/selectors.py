from .models import User


def get_user_by_email(email):
    """The user for `email`, normalised the way it was stored, or None."""
    return User.objects.filter(email=User.objects.normalize_email(email)).first()
