"""Briefs per goal: what the organization sells and to whom (clients), and what it bids
for (tenders). Grant preferences stay where they were."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from .. import services
from ..selectors import setup_status

pytestmark = pytest.mark.django_db


def url(name, organization):
    return reverse(f"v1:organizations:{name}", kwargs={"organization_id": organization.pk})


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def set_goals(organization, user, goals):
    services.update_organization(organization_id=organization.pk, actor=user, goals=goals)
    organization.refresh_from_db()


def step_keys(organization):
    return [step["key"] for step in setup_status(organization)["steps"]]


# --- Clients brief -----------------------------------------------------------------------


def test_a_new_clients_brief_is_empty(auth_client, organization):
    response = auth_client.get(url("client-brief", organization))
    assert response.status_code == 200
    assert response.data["services"] == []
    assert response.data["signals"] == []


def test_admin_fills_the_clients_brief(auth_client, organization):
    response = auth_client.patch(
        url("client-brief", organization),
        {
            "services": [
                {"name": "Websites", "price": "from KES 40,000"},
                {"name": " websites ", "description": "duplicate"},
                {"name": "AI chatbots", "description": "WhatsApp and web chat"},
            ],
            "customer_types": ["hospitality", "health", "hospitality"],
            "customer_keywords": ["boutique hotels"],
            "customer_sizes": ["small", "medium"],
            "target_countries": ["ke", "UG"],
            "target_cities": ["Nairobi", "Mombasa"],
            "signals": ["no_website", "no_online_booking"],
            "notes": "We deliver in four weeks.",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["services"] == [
        {"name": "Websites", "description": "", "price": "from KES 40,000"},
        {"name": "AI chatbots", "description": "WhatsApp and web chat", "price": ""},
    ]
    assert response.data["customer_types"] == ["hospitality", "health"]
    assert response.data["target_countries"] == ["KE", "UG"]
    event = organization.events.get(action="client_brief.updated")
    assert "services" in event.metadata["fields"]


@pytest.mark.parametrize(
    "payload",
    [
        {"customer_types": ["spaceships"]},
        {"signals": ["vibes"]},
        {"customer_sizes": ["huge"]},
        {"services": [{"name": ""}]},
        {"services": [{"name": f"Service {n}"} for n in range(21)]},
        {"target_countries": ["Kenya"]},
    ],
)
def test_clients_brief_validation(auth_client, organization, payload):
    response = auth_client.patch(url("client-brief", organization), payload, format="json")
    assert response.status_code == 400


def test_members_read_briefs_but_only_admins_edit_them(organization, membership):
    client = as_user(membership.user)
    for name in ("client-brief", "tender-brief"):
        assert client.get(url(name, organization)).status_code == 200
        response = client.patch(url(name, organization), {"notes": "x"}, format="json")
        assert response.status_code == 403


# --- Tenders brief -----------------------------------------------------------------------


def test_admin_fills_the_tenders_brief(auth_client, organization):
    response = auth_client.patch(
        url("tender-brief", organization),
        {
            "categories": ["software", "it_services"],
            "keywords": ["ERP", "website"],
            "buyer_types": ["county_government", "international"],
            "target_countries": ["KE"],
            "min_value": "500000",
            "max_value": "20000000",
            "currency": "kes",
            "eligibility": ["agpo_youth", "tax_compliant"],
            "certifications": ["ICTA 1"],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["currency"] == "KES"
    assert response.data["categories"] == ["software", "it_services"]
    assert organization.events.filter(action="tender_brief.updated").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"categories": ["weapons"]},
        {"buyer_types": ["aliens"]},
        {"eligibility": ["agpo_martians"]},
        {"min_value": "10", "max_value": "5"},
        {"currency": "shillings"},
        {"min_value": "-1"},
    ],
)
def test_tenders_brief_validation(auth_client, organization, payload):
    response = auth_client.patch(url("tender-brief", organization), payload, format="json")
    assert response.status_code == 400


def test_a_value_range_is_checked_against_the_stored_bound(auth_client, organization):
    endpoint = url("tender-brief", organization)
    assert auth_client.patch(endpoint, {"max_value": "100"}, format="json").status_code == 200
    assert auth_client.patch(endpoint, {"min_value": "200"}, format="json").status_code == 400


# --- The setup checklist asks for each goal's brief --------------------------------------


def test_without_goals_the_checklist_asks_for_grant_preferences(organization):
    assert "preferences" in step_keys(organization)
    assert "client_brief" not in step_keys(organization)


def test_the_checklist_asks_for_the_brief_of_each_goal(user, organization):
    set_goals(organization, user, ["tenders", "clients"])
    keys = step_keys(organization)
    assert "preferences" not in keys
    assert keys.index("client_brief") < keys.index("tender_brief")  # stable goal order


def test_ready_for_matching_once_every_goals_brief_is_filled(user, organization):
    services.update_organization(
        organization_id=organization.pk,
        actor=user,
        organization_type="sme",
        description="We build websites and AI apps.",
        offerings="Websites, AI chatbots",
        sectors=["technology"],
        goals=["clients", "tenders"],
    )
    organization.refresh_from_db()
    assert not setup_status(organization)["ready_for_matching"]

    services.update_client_brief(
        organization_id=organization.pk,
        actor=user,
        services=[{"name": "Websites", "description": "", "price": ""}],
        customer_types=["hospitality"],
    )
    assert not setup_status(organization)["ready_for_matching"]  # tenders brief missing

    services.update_tender_brief(
        organization_id=organization.pk, actor=user, categories=["software"]
    )
    assert setup_status(organization)["ready_for_matching"]


def test_customer_keywords_alone_describe_who_to_sell_to(user, organization):
    set_goals(organization, user, ["clients"])
    services.update_client_brief(
        organization_id=organization.pk,
        actor=user,
        services=[{"name": "Websites", "description": "", "price": ""}],
        customer_keywords=["boutique hotels"],
    )
    step = next(s for s in setup_status(organization)["steps"] if s["key"] == "client_brief")
    assert step["done"]
