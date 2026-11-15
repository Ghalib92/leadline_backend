"""A run sees the brief of its goal: its keywords and the facts its agents reason with."""

import pytest

from apps.discovery import selectors, services
from apps.organizations import services as organization_services

from .factories import make_agent

pytestmark = pytest.mark.django_db


@pytest.fixture
def briefs(user, organization):
    """Grant preferences, a tenders brief and a clients brief, all filled."""
    organization_services.update_preferences(
        organization_id=organization.pk,
        actor=user,
        keywords=["data skills"],
        excluded_keywords=["loan"],
        preferred_funders=["Northbridge Foundation"],
    )
    organization_services.update_tender_brief(
        organization_id=organization.pk,
        actor=user,
        categories=["software", "training"],
        keywords=["ERP"],
        excluded_keywords=["construction"],
        buyer_types=["county_government"],
        min_value="500000",
        eligibility=["agpo_youth"],
    )
    organization_services.update_client_brief(
        organization_id=organization.pk,
        actor=user,
        services=[{"name": "Websites", "description": "", "price": "from KES 40,000"}],
        customer_types=["hospitality"],
        signals=["no_website"],
        excluded_keywords=["franchise"],
    )
    return organization


def set_goals(organization, user, goals):
    organization_services.update_organization(
        organization_id=organization.pk, actor=user, goals=goals
    )
    organization.refresh_from_db()


def test_a_tenders_run_sees_the_tenders_brief_not_the_grant_preferences(briefs):
    profile = selectors.profile_context(briefs, "tenders")
    assert profile["goal"] == "tenders"
    assert profile["tender_brief"] == {
        "categories": ["Software, systems & apps", "Training & capacity building"],
        "keywords": ["ERP"],
        "buyer_types": ["County or local government"],
        "currency": "USD",
        "min_value": "500000.00",
        "eligibility": ["AGPO: youth-owned"],
    }
    assert profile["excluded_keywords"] == ["construction"]
    assert "preferred_funders" not in profile
    assert "client_brief" not in profile


def test_a_grants_run_keeps_the_grant_preferences(briefs):
    profile = selectors.profile_context(briefs, "grants")
    assert profile["preferred_funders"] == ["Northbridge Foundation"]
    assert profile["excluded_keywords"] == ["loan"]
    assert "tender_brief" not in profile


def test_a_clients_brief_reads_as_words(briefs):
    brief = selectors.profile_context(briefs, "clients")["client_brief"]
    assert brief == {
        "services": [{"name": "Websites", "price": "from KES 40,000"}],
        "customer_types": ["Hotels, lodges & guest houses"],
        "signs_they_need_us": ["No website"],
    }


def test_without_a_goal_every_goal_the_organization_works_on_is_included(user, briefs):
    set_goals(briefs, user, ["clients", "tenders"])
    profile = selectors.profile_context(briefs)
    assert profile.keys() >= {"client_brief", "tender_brief"}
    assert "preferred_funders" not in profile
    assert profile["excluded_keywords"] == ["construction", "franchise"]


def test_an_organization_without_goals_keeps_its_grant_context(briefs):
    profile = selectors.profile_context(briefs)
    assert profile["preferred_funders"] == ["Northbridge Foundation"]
    assert "tender_brief" not in profile


def test_keywords_come_from_the_goals_brief(briefs):
    assert selectors.search_keywords(briefs, "grants") == ["data skills"]
    assert selectors.search_keywords(briefs, "tenders") == ["ERP"]
    assert selectors.search_keywords(briefs, "clients") == []  # types are searched as such


def test_a_tenders_brief_without_keywords_searches_its_categories(user, organization):
    organization_services.update_tender_brief(
        organization_id=organization.pk, actor=user, categories=["software"]
    )
    assert selectors.search_keywords(organization, "tenders") == ["Software, systems & apps"]


def test_a_tenders_agents_run_uses_the_tenders_keywords(user, briefs):
    agent = make_agent(
        briefs, user, goal="tenders", opportunity_types=[], sources=[], keywords=["portal"]
    )
    config = services._resolve_run_config(briefs, agent, None)
    assert config["keywords"] == ["ERP", "portal"]
    assert config["origins"]["keywords"] == {"ERP": "organization", "portal": "agent"}
