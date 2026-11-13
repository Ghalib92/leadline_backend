"""Shared test data for discovery and opportunities."""

from datetime import timedelta

from django.utils import timezone

from apps.discovery.models import SearchAgent
from apps.discovery.selectors import run_settings_for
from apps.opportunities.dedupe import title_key, url_key
from apps.opportunities.models import Opportunity
from apps.organizations import services as organization_services


def ready_organization(user, name="Acme Africa"):
    """An organization whose setup is complete, so it may run searches."""
    organization = organization_services.create_organization(actor=user, name=name, country="KE")
    organization_services.update_organization(
        organization_id=organization.pk,
        actor=user,
        organization_type="ngo",
        description="We train young people in data skills.",
        mission="Close the data skills gap.",
        sectors=["education"],
        operating_countries=["KE"],
    )
    organization_services.update_preferences(
        organization_id=organization.pk,
        actor=user,
        opportunity_types=["grant", "tender"],
        geographic_scopes=["national"],
    )
    organization.refresh_from_db()
    return organization


def search_settings(organization, **fields):
    """Set the organization's search settings, which every agent's run also uses."""
    run_settings = run_settings_for(organization)
    for field, value in fields.items():
        setattr(run_settings, field, value)
    run_settings.save()
    return run_settings


def make_agent(organization, owner, **overrides):
    data = {
        "name": "Education grants",
        "opportunity_types": ["grant", "tender"],
        "keywords": ["data skills"],
        "sources": ["web", "grants"],
        **overrides,
    }
    return SearchAgent.objects.create(organization=organization, owner=owner, **data)


def make_opportunity(organization, *, title="Data Skills Grant", url=None, **overrides):
    url = url or f"https://example.org/{title.lower().replace(' ', '-')}"
    data = {
        "opportunity_type": "grant",
        "funder": "Northbridge Foundation",
        "source_url": url,
        "source_name": "example.org",
        "match_score": 70,
        "match_reasons": ["Fits your focus"],
        **overrides,
    }
    return Opportunity.objects.create(
        organization=organization,
        title=title,
        dedupe_key=url_key(url),
        title_key=title_key(title, data["funder"]),
        **data,
    )


def days(n):
    return timezone.now() + timedelta(days=n)
