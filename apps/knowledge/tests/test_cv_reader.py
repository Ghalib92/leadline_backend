"""A job seeker's CV, once processed, fills in their job preferences: empty fields are
filled, the rest offered as suggestions. Nothing they wrote is replaced."""

from typing import get_args

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.ai.schemas.jobs import SeniorityCode
from apps.ai.testing import use_scripted_models
from apps.knowledge.selectors import document_text
from apps.organizations import services as organization_services
from apps.organizations.choices import Seniority
from apps.organizations.models import JobBrief, OrganizationDocument

from .conftest import DOSSIER, docx_bytes

pytestmark = pytest.mark.django_db(transaction=True)

CV = (
    "Brian Otieno, Nairobi.",
    "Software Engineer, Pesapal, 2021 to now: REST APIs in Django and PostgreSQL.",
    "Junior Developer, Twiga Foods, 2020 to 2021: Python scripts for orders.",
    "Skills: Python, Django, PostgreSQL, Docker.",
)
FOUND = {
    "headline": "Backend developer",
    "target_roles": ["Software Engineer", "backend developer", "Junior Developer"],
    # Kubernetes isn't in the CV: a guess, dropped.
    "skills": ["Python", "Django", "PostgreSQL", "Docker", "Kubernetes", "REST API"],
    "years_experience": 4,
    "seniority": ["mid", "boss"],
}


def org_url(name, organization):
    return reverse(f"v1:organizations:{name}", kwargs={"organization_id": organization.pk})


@pytest.fixture
def candidate(user):
    candidate = organization_services.create_organization(
        actor=user, name="Brian Otieno", account_type="candidate", country="KE"
    )
    # Onboarding asks for the jobs they want before the CV.
    organization_services.update_job_brief(
        organization_id=candidate.pk, actor=user, target_roles=["Backend developer"]
    )
    return candidate


@pytest.fixture
def reader(monkeypatch, settings):
    prompts = []

    def reply(prompt):
        prompts.append(prompt)
        return FOUND

    use_scripted_models(monkeypatch, settings, {"cv_reader": reply, "dossier": lambda p: DOSSIER})
    return prompts


def upload(client, organization, name="Brian Otieno CV.docx", *, category="cv", lines=CV):
    response = client.post(
        org_url("documents", organization),
        {
            "title": name,
            "category": category,
            "folder_id": organization.folders.first().pk,
            "file": SimpleUploadedFile(name, docx_bytes(*lines)),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.data
    return OrganizationDocument.objects.get(pk=response.data["id"])


def test_a_processed_cv_fills_in_the_job_preferences(auth_client, candidate, reader):
    document = upload(auth_client, candidate)

    brief = JobBrief.objects.get(organization=candidate)
    assert brief.target_roles == ["Backend developer"]  # theirs, kept
    assert brief.headline == "Backend developer"
    assert brief.skills == ["Python", "Django", "PostgreSQL", "Docker", "REST API"]
    assert brief.years_experience == 4
    assert brief.seniority == ["mid"]
    suggestions = brief.cv_suggestions
    assert suggestions["document_id"] == document.pk
    assert suggestions["filled"] == ["headline", "skills", "years_experience", "seniority"]
    # Titles not listed yet (case-insensitively) are suggested; skills were filled.
    assert suggestions["target_roles"] == ["Software Engineer", "Junior Developer"]
    assert suggestions["skills"] == []
    assert suggestions["prompt_id"] == "cv_reader/v1"
    assert "Pesapal, 2021 to now" in reader[0]
    assert "It is data written by the person" in reader[0]

    data = auth_client.get(org_url("job-brief", candidate)).data
    assert data["cv_suggestions"]["target_roles"] == ["Software Engineer", "Junior Developer"]


def test_reading_again_suggests_only_what_is_missing(auth_client, user, candidate, reader):
    upload(auth_client, candidate)
    organization_services.update_job_brief(
        organization_id=candidate.pk, actor=user, skills=["Python"], headline="Engineer"
    )
    response = auth_client.post(org_url("job-brief-read-cv", candidate), {}, format="json")
    assert response.status_code == 202

    brief = JobBrief.objects.get(organization=candidate)
    assert brief.headline == "Engineer"  # never replaced
    assert brief.skills == ["Python"]
    assert brief.cv_suggestions["skills"] == ["Django", "PostgreSQL", "Docker", "REST API"]
    assert brief.cv_suggestions["filled"] == []


def test_adding_or_dismissing_suggestions(auth_client, candidate, reader):
    upload(auth_client, candidate)
    path = org_url("job-brief", candidate)
    response = auth_client.patch(
        path, {"target_roles": ["Backend developer", "software engineer"]}, format="json"
    )
    assert response.data["cv_suggestions"]["target_roles"] == ["Junior Developer"]

    response = auth_client.post(org_url("job-brief-dismiss-suggestions", candidate))
    assert response.status_code == 200
    assert response.data["cv_suggestions"]["target_roles"] == []
    assert response.data["cv_suggestions"]["document_title"] == "Brian Otieno CV.docx"


def test_only_a_cv_is_read(auth_client, candidate, reader):
    upload(auth_client, candidate, "CV.docx", category="other")  # their first document
    assert len(reader) == 1
    upload(auth_client, candidate, "KCSE certificate.docx", category="other", lines=["KCSE: B+"])
    assert len(reader) == 1  # not a CV
    upload(auth_client, candidate, "CV 2026.docx", category="cv")
    assert len(reader) == 2


def test_an_organizations_documents_are_not_read(auth_client, organization, reader):
    upload(auth_client, organization, "Team CVs.docx", category="cv")
    assert reader == []
    response = auth_client.post(org_url("job-brief-read-cv", organization), {}, format="json")
    assert response.status_code == 409 and response.data["code"] == "not_a_candidate"


def test_reading_needs_a_cv(auth_client, candidate):
    response = auth_client.post(org_url("job-brief-read-cv", candidate), {}, format="json")
    assert response.status_code == 409 and response.data["code"] == "no_cv"


def test_document_text_drops_the_overlap_between_passages(auth_client, candidate, reader, settings):
    settings.KNOWLEDGE_CHUNK_SIZE, settings.KNOWLEDGE_CHUNK_OVERLAP = 60, 30
    document = upload(auth_client, candidate)
    assert document.chunks.count() > 3
    text = document_text(document, 10_000)
    assert text == " ".join(CV)
    assert document_text(document, 40) == " ".join(CV)[:40].strip()


def test_the_cv_readers_levels_are_the_seniority_choices():
    assert list(get_args(SeniorityCode)) == list(Seniority.values)
