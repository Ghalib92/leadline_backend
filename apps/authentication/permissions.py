from rest_framework.permissions import BasePermission


class IsVerified(BasePermission):
    """Signed in and has confirmed their email address."""

    message = "Please verify your email address."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_verified)