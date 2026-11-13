"""Multipart transactional emails. Template content is escaped by Django."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone


def send_templated_email(*, to, subject, template, context=None, attachments=()):
    """`attachments`: (filename, content, mimetype) tuples."""
    values = {
        "product_name": settings.PRODUCT_NAME,
        "frontend_url": settings.FRONTEND_URL.rstrip("/"),
        "year": timezone.now().year,
        **(context or {}),
    }
    message = EmailMultiAlternatives(
        subject=" ".join(subject.splitlines()),
        body=render_to_string(f"{template}.txt", values),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    message.attach_alternative(render_to_string(f"{template}.html", values), "text/html")
    for filename, content, mimetype in attachments:
        message.attach(filename, content, mimetype)
    sent = message.send(fail_silently=False)
    if not sent:
        raise RuntimeError("Email backend did not accept the message.")
    return sent
