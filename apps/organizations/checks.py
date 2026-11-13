"""System checks for settings that end up in emailed links or upload handling."""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, register


@register()
def organization_configuration(app_configs, **kwargs):
    errors = []
    try:
        frontend = urlsplit(settings.FRONTEND_URL)
        frontend.port  # noqa: B018 — raises ValueError for malformed ports
        valid_frontend = (
            frontend.scheme in {"http", "https"}
            and bool(frontend.hostname)
            and not frontend.query
            and not frontend.fragment
        )
    except ValueError:
        valid_frontend = False
    if not valid_frontend:
        errors.append(
            Error(
                "FRONTEND_URL must be an absolute http(s) URL without a query or fragment.",
                id="organizations.E001",
            )
        )

    path = settings.ORGANIZATION_INVITATION_PATH
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        errors.append(
            Error(
                "ORGANIZATION_INVITATION_PATH must be a path starting with a single '/'.",
                id="organizations.E002",
            )
        )
    if settings.ORGANIZATION_INVITATION_EXPIRY_HOURS < 1:
        errors.append(
            Error(
                "ORGANIZATION_INVITATION_EXPIRY_HOURS must be at least 1.",
                id="organizations.E003",
            )
        )
    if settings.ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS < 0:
        errors.append(
            Error(
                "ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS cannot be negative.",
                id="organizations.E004",
            )
        )
    if settings.ORGANIZATION_DOCUMENT_MAX_UPLOAD_MB < 1:
        errors.append(
            Error(
                "ORGANIZATION_DOCUMENT_MAX_UPLOAD_MB must be at least 1.",
                id="organizations.E005",
            )
        )
    return errors
