from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers


def validate_new_password(password, user=None, field=None):
    """
    Run AUTH_PASSWORD_VALIDATORS, surfacing failures as DRF errors — keyed by
    `field` when called from `Serializer.validate()` rather than a field method.
    """
    try:
        password_validation.validate_password(password, user=user)
    except DjangoValidationError as exc:
        messages = list(exc.messages)
        raise serializers.ValidationError({field: messages} if field else messages) from exc
    return password


def validate_otp_code(value):
    if not value.isdigit():
        raise serializers.ValidationError("Code must be numeric.")
    return value
