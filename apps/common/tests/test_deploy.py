"""What the deployment relies on: a health check, production settings that start, and
throttles that tell visitors apart behind the UI server."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.cache import cache
from django.urls import reverse

BASE_DIR = Path(__file__).resolve().parents[3]


@pytest.mark.django_db
def test_health_is_open_and_checks_the_database(api_client):
    response = api_client.get(reverse("v1:health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_health_reports_a_database_it_cannot_reach(api_client, monkeypatch):
    from apps.common.api import health

    def broken():
        raise OSError("connection refused")

    monkeypatch.setattr(health.connection, "cursor", broken)

    response = api_client.get(reverse("v1:health"))

    assert response.status_code == 503


def test_production_settings_pass_the_deploy_checks():
    """The settings the server runs with import (every app installed) and pass
    `check --deploy` with the values in deploy/env.production.example."""
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DB_"))},
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DJANGO_SECRET_KEY": "x" * 60,
        "DJANGO_ALLOWED_HOSTS": "leadline-api,localhost",
        "EMAIL_HOST": "smtp.example.com",
        "EMAIL_HOST_USER": "noreply@example.com",
        "EMAIL_HOST_PASSWORD": "secret",
        "SECURE_SSL_REDIRECT": "False",
    }
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "ERROR"],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-3000:]


@pytest.mark.django_db
def test_behind_the_ui_server_each_visitor_has_their_own_login_throttle(
    api_client, settings, monkeypatch
):
    """Every request reaches Django from the UI server, which vouches for the
    visitor's address with the shared secret; that address keys the throttle."""
    from rest_framework.throttling import ScopedRateThrottle

    settings.UI_PROXY_SECRET = "shared-secret"
    # Tests turn throttles off, and DRF reads the rates once, at import.
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"login": "2/minute"})
    cache.clear()
    url = reverse("v1:authentication:login")
    body = {"email": "nobody@example.com", "password": "wrong-password"}

    def login(visitor):
        return api_client.post(
            url,
            body,
            format="json",
            REMOTE_ADDR="76.76.21.21",  # the UI server (Vercel)
            HTTP_X_LEADLINE_PROXY="shared-secret",
            HTTP_X_LEADLINE_CLIENT_IP=visitor,
        ).status_code

    assert [login("41.90.1.1") for _ in range(3)] == [401, 401, 429]
    assert login("41.90.2.2") == 401
    cache.clear()


def client_ip(settings, *, secret="", header="", **meta):
    from apps.common.middleware import ClientIPMiddleware

    settings.UI_PROXY_SECRET = secret
    settings.TRUSTED_CLIENT_IP_HEADER = header
    return ClientIPMiddleware.client_ip({"REMOTE_ADDR": "172.18.0.2", **meta})


def test_the_visitor_address_is_believed_only_with_the_secret(settings):
    vouched = {"HTTP_X_LEADLINE_PROXY": "s3cret", "HTTP_X_LEADLINE_CLIENT_IP": "41.90.1.1"}

    assert client_ip(settings, secret="s3cret", **vouched) == "41.90.1.1"
    # Anyone can call the public API and claim an address: without the secret, or
    # with no secret configured, the claim is ignored.
    assert client_ip(settings, secret="other", **vouched) == "172.18.0.2"
    assert client_ip(settings, secret="", **vouched) == "172.18.0.2"
    # Not an address at all.
    garbage = {**vouched, "HTTP_X_LEADLINE_CLIENT_IP": "41.90.1.1, 10.0.0.1"}
    assert client_ip(settings, secret="s3cret", **garbage) == "172.18.0.2"


def test_the_edge_proxy_reports_the_connecting_address(settings):
    # Caddy sets X-Real-IP; X-Forwarded-For is client-written and never used.
    meta = {"HTTP_X_REAL_IP": "102.68.1.1", "HTTP_X_FORWARDED_FOR": "1.2.3.4"}

    assert client_ip(settings, header="X-Real-IP", **meta) == "102.68.1.1"
    assert client_ip(settings, **meta) == "172.18.0.2"
