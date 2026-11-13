"""The demo seed builds a complete, consistent organization through the API."""

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.authentication.models import User
from apps.common.management.commands.seed import ADMIN, MEMBER, ORG_NAME
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


def test_seed_creates_a_demo_the_api_can_serve():
    call_command("seed")
    org = Organization.objects.get(name=ORG_NAME)
    admin = User.objects.get(email=ADMIN[0])
    assert admin.is_verified and admin.check_password("LeadlineDemo2026!")
    client = APIClient()
    client.force_authenticate(user=admin)
    base = f"/api/v1/organizations/{org.pk}"
    assert client.get(f"{base}/setup/").json()["ready_for_matching"] is True
    assert client.get(f"{base}/opportunities/").json()["count"] == 12
    pending = client.get(f"{base}/opportunities/", {"review": "pending"}).json()["count"]
    assert pending == 7
    runs = client.get(f"{base}/runs/").json()["results"]
    assert {run["status"] for run in runs} == {"succeeded", "partial", "failed"}
    detail = client.get(f"{base}/runs/{runs[0]['id']}/").json()
    assert detail["steps"][0]["events"] and detail["candidates"]
    insights = client.get(f"{base}/insights/", {"days": 90}).json()
    assert insights["reviews"]["approved"] == 3 and insights["rejection_reasons"]
    youth = client.get(f"{base}/opportunities/", {"search": "Youth Data Skills"}).json()
    proposal = client.get(f"{base}/opportunities/{youth['results'][0]['id']}/proposal/").json()
    assert proposal["version"] == 2 and proposal["review"]["score"] == 74
    queue = client.get(f"{base}/outreach/", {"status": "draft"}).json()["results"]
    assert len(queue) == 1 and queue[0]["to_email"] == "procurement@example.org"
    assert len(client.get(f"{base}/deadlines/").json()) >= 4

    member = User.objects.get(email=MEMBER[0])
    assert org.memberships.get(user=member).role == "member"

    call_command("seed")  # second run: no duplicates
    call_command("seed", "--reset")
    assert Organization.objects.filter(name=ORG_NAME).count() == 1
