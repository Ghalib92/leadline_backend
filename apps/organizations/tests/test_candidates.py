"""Job seekers: a workspace of their own, with job preferences and their CV."""

import pytest
from django.urls import reverse

from apps.discovery.selectors import job_search_plan, profile_context

from ..models import Organization
from ..selectors import setup_status

pytestmark = pytest.mark.django_db


def url(name, organization):
    return reverse(f"v1:organizations:{name}", kwargs={"organization_id": organization.pk})


def create(client, **data):
    return client.post(reverse("v1:organizations:list"), data, format="json")


@pytest.fixture
def candidate(auth_client):
    response = create(
        auth_client,
        name="Amina Otieno",
        account_type="candidate",
        country="KE",
        city="Nairobi",
        description="Backend developer, 4 years of Python and Django.",
    )
    assert response.status_code == 201, response.data
    return Organization.objects.get(pk=response.data["id"])


def test_a_job_seeker_gets_a_workspace_for_jobs(auth_client, candidate):
    data = auth_client.get(url("detail", candidate)).data
    assert data["account_type"] == "candidate"
    assert data["goals"] == ["jobs"]  # set for them
    assert list(candidate.folders.values_list("name", flat=True)) == ["My documents"]
    goals = auth_client.get(
        reverse("v1:discovery:run-settings", kwargs={"organization_id": candidate.pk})
    ).data["goals"]
    assert [goal["key"] for goal in goals] == ["jobs"]
    assert goals[0]["opportunity_types"] == ["job"]
    assert goals[0]["sources"] == ["jobs", "web"]


def test_goals_follow_the_account_type(auth_client, candidate, organization):
    bad = auth_client.patch(url("detail", candidate), {"goals": ["tenders"]}, format="json")
    assert bad.status_code == 400 and "job seeker" in str(bad.data["goals"])
    response = auth_client.patch(url("detail", organization), {"goals": ["jobs"]}, format="json")
    assert response.status_code == 400 and "organization" in str(response.data["goals"])
    business = create(auth_client, name="Shop", goals=["jobs"])
    assert business.status_code == 400
    # The account type is chosen once, at creation.
    change = auth_client.patch(
        url("detail", candidate), {"account_type": "organization"}, format="json"
    )
    assert change.status_code == 400


def test_job_preferences(auth_client, candidate):
    path = url("job-brief", candidate)
    assert auth_client.get(path).data["target_roles"] == []
    response = auth_client.patch(
        path,
        {
            "headline": "Backend developer",
            "target_roles": ["Backend developer", "Python developer"],
            "seniority": ["mid", "senior"],
            "employment_types": ["full_time", "contract"],
            "workplaces": ["remote", "hybrid"],
            "target_countries": ["ke"],
            "target_cities": ["Nairobi"],
            "skills": ["Python", "Django", "PostgreSQL"],
            "years_experience": 4,
            "salary_min": 250000,
            "salary_currency": "kes",
            "companies": ["https://jobs.lever.co/acme", " https://jobs.lever.co/acme", "ashby:x"],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["salary_currency"] == "KES"
    assert response.data["target_countries"] == ["KE"]
    assert response.data["companies"] == ["https://jobs.lever.co/acme", "ashby:x"]
    bad = auth_client.patch(path, {"seniority": ["wizard"]}, format="json")
    assert bad.status_code == 400

    context = profile_context(candidate, "jobs")["job_brief"]
    assert context["seniority"] == ["Mid level", "Senior"]
    assert context["workplaces"] == ["Remote", "Hybrid"]
    assert context["minimum_monthly_salary"] == "250,000 KES"
    assert job_search_plan(candidate) == {
        "companies": ["https://jobs.lever.co/acme", "ashby:x"],
        "countries": ["KE"],
        "cities": ["Nairobi"],
        "workplaces": ["remote", "hybrid"],
    }


def test_a_job_seekers_checklist(auth_client, candidate):
    status = setup_status(candidate)
    assert [step["key"] for step in status["steps"]] == ["basics", "job_brief", "cv", "skills"]
    assert not status["ready_for_matching"]  # no roles, no CV yet
    auth_client.patch(url("job-brief", candidate), {"target_roles": ["Backend"]}, format="json")
    candidate.documents.create(
        folder=candidate.folders.first(),
        title="CV",
        file="cv.pdf",
        original_filename="cv.pdf",
        size=1,
    )
    assert setup_status(candidate)["ready_for_matching"]


def test_without_preferences_the_profile_place_is_searched(candidate):
    assert job_search_plan(candidate) == {
        "companies": [],
        "countries": ["KE"],
        "cities": ["Nairobi"],
        "workplaces": [],
    }


def test_a_jobs_search_always_reads_career_pages(auth_client, candidate):
    """The workspace's general search settings (web, grants, procurement by default)
    never mention jobs: a jobs run still searches its own sources."""
    from apps.discovery.models import DiscoveryRun

    auth_client.patch(url("job-brief", candidate), {"target_roles": ["Backend"]}, format="json")
    candidate.documents.create(
        folder=candidate.folders.first(),
        title="CV",
        file="cv.pdf",
        original_filename="cv.pdf",
        size=1,
    )
    response = auth_client.post(
        reverse("v1:discovery:runs", kwargs={"organization_id": candidate.pk}), {}, format="json"
    )
    assert response.status_code == 202, response.data
    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.config["goal"] == "jobs"
    assert run.config["sources"][0] == "jobs"
    assert run.config["opportunity_types"] == ["job"]
