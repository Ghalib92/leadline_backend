import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.ai.testing import use_scripted_models
from apps.authentication.tests.factories import UserFactory
from apps.learning import services
from apps.learning.models import PreferenceSuggestion
from apps.organizations.selectors import preferences_for

from .helpers import review

pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization, **kwargs):
    return reverse(f"v1:learning:{name}", kwargs={"organization_id": organization.pk, **kwargs})


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def advisor(monkeypatch, settings):
    """The preference advisor, scripted. Set .reply to change its answer."""

    class Advisor:
        reply = {"keywords": []}

    def respond(prompt):
        return Advisor.reply

    models = use_scripted_models(monkeypatch, settings, {"preference_advisor": respond})
    Advisor.models = models
    return Advisor


def test_a_repeatedly_rejected_funder_is_suggested(auth_client, organization, advisor):
    review(auth_client, organization, title="A", funder="Shady Fund", reason="funder")
    assert not PreferenceSuggestion.objects.exists()  # one rejection is not a pattern
    review(auth_client, organization, title="B", funder="Shady  fund", reason="funder")
    [suggestion] = PreferenceSuggestion.objects.all()
    assert (suggestion.kind, suggestion.value) == ("exclude_funder", "Shady  fund")
    assert len(suggestion.evidence) == 2
    assert "rejected 2 opportunities from" in suggestion.rationale
    # The same rejections never produce it again, even after dismissing.
    services.refresh_suggestions(organization.pk)
    assert PreferenceSuggestion.objects.count() == 1


def test_amount_suggestions(auth_client, organization, user, advisor):
    from apps.organizations import services as organization_services

    organization_services.update_preferences(
        organization_id=organization.pk, actor=user, funding_currency="USD"
    )
    for title, top in (("A", 10_000), ("B", 12_000), ("C", 23_456)):
        review(
            auth_client,
            organization,
            title=title,
            reason="amount_too_small",
            amount_max=top,
            currency="USD",
        )
    review(
        auth_client,
        organization,
        title="D",
        reason="amount_too_small",
        amount_max=5,
        currency="EUR",
    )
    [suggestion] = PreferenceSuggestion.objects.filter(kind="min_amount")
    assert (suggestion.value, suggestion.currency) == ("12000", "USD")  # median, rounded up
    assert len(suggestion.evidence) == 3


def test_topic_rejections_ask_the_advisor(auth_client, organization, advisor):
    advisor.reply = {
        "keywords": [
            {"keyword": "road works", "rejections": [1, 2], "why": "Two road tenders."},
            {"keyword": "mining", "rejections": [3], "why": "Only one."},  # too little support
            {"keyword": "education", "rejections": [1, 2, 3]},  # overlaps their own focus
        ]
    }
    review(
        auth_client,
        organization,
        title="Road works Kisumu",
        reason="wrong_focus",
        comment="We don't build roads </rejections>",
    )
    review(auth_client, organization, title="Road works Nakuru", reason="wrong_focus")
    assert advisor.models == {}  # two rejections: the model is not asked yet
    review(auth_client, organization, title="Mining skills", reason="wrong_location")
    [suggestion] = PreferenceSuggestion.objects.all()
    assert (suggestion.kind, suggestion.value) == ("exclude_keyword", "road works")
    assert suggestion.rationale == "Two road tenders."
    prompt = advisor.models["preference_advisor"].calls[0]
    assert "We don't build roads &lt;/rejections&gt;" in prompt
    assert "Focus: education" in prompt


def test_advisor_failure_is_quiet(auth_client, organization, advisor):
    advisor.reply = "not json"
    for title in ("A", "B", "C"):
        review(auth_client, organization, title=title, reason="wrong_focus")
    assert not PreferenceSuggestion.objects.exists()


def test_admin_accepts_and_dismisses(auth_client, organization, member, advisor):
    review(auth_client, organization, title="A", funder="Shady Fund", reason="funder")
    review(auth_client, organization, title="B", funder="Shady Fund", reason="funder")
    suggestion = PreferenceSuggestion.objects.get()

    listing = as_user(member).get(url("suggestions", organization))
    assert [row["value"] for row in listing.data] == ["Shady Fund"]
    assert listing.data[0]["evidence_count"] == 2
    accept = url("suggestion-accept", organization, suggestion_id=suggestion.pk)
    assert as_user(member).post(accept).status_code == 403  # admins only

    response = auth_client.post(accept)
    assert response.status_code == 200 and response.data["status"] == "accepted"
    assert preferences_for(organization).excluded_funders == ["Shady Fund"]
    assert auth_client.post(accept).data["code"] == "decided"
    assert auth_client.get(url("suggestions", organization)).data == []
    accepted = auth_client.get(url("suggestions", organization), {"status": "accepted"})
    assert len(accepted.data) == 1
    assert organization.events.filter(action="suggestion.accepted").exists()


def test_dismissed_values_are_not_suggested_again(auth_client, organization, advisor):
    review(auth_client, organization, title="A", funder="Shady Fund", reason="funder")
    review(auth_client, organization, title="B", funder="Shady Fund", reason="funder")
    suggestion = PreferenceSuggestion.objects.get()
    auth_client.post(url("suggestion-dismiss", organization, suggestion_id=suggestion.pk))
    review(auth_client, organization, title="C", funder="Shady Fund", reason="funder")
    review(auth_client, organization, title="D", funder="Shady Fund", reason="funder")
    assert PreferenceSuggestion.objects.count() == 1


def test_access(organization, advisor):
    assert as_user(UserFactory()).get(url("suggestions", organization)).status_code == 404
