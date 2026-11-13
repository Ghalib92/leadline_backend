from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.tests.factories import UserFactory
from apps.discovery.models import DiscoveryRun
from apps.discovery.tests.factories import make_agent, make_opportunity
from apps.opportunities.models import OpportunityReview
from apps.opportunities.signals import opportunity_approved
from apps.organizations import services as organization_services

# Approval sends its signal on commit, so these tests need real transactions.
pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization, **kwargs):
    return reverse(
        f"v1:opportunities:{name}", kwargs={"organization_id": organization.pk, **kwargs}
    )


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def review(client, organization, opportunity, **body):
    return client.post(
        url("reviews", organization, opportunity_id=opportunity.pk), body, format="json"
    )


@pytest.fixture
def approvals():
    received = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    opportunity_approved.connect(receiver, dispatch_uid="test-approvals")
    yield received
    opportunity_approved.disconnect(dispatch_uid="test-approvals")


@pytest.fixture
def opportunity(user, organization):
    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        status="succeeded",
        prompt_versions={"judge": "judge/v1"},
        models_used={"judge": "openai/gpt-oss-120b"},
    )
    return make_opportunity(
        organization, agent=agent, run=run, match_score=78, deadline=timezone.now()
    )


# ---------------------------------------------------------------------------
# Approve and reject
# ---------------------------------------------------------------------------


def test_member_approves_and_the_proposal_writer_is_told(
    organization, member, opportunity, approvals
):
    response = review(
        as_user(member),
        organization,
        opportunity,
        decision="approve",
        comment="Strong fit for our Nairobi programme",
    )
    assert response.status_code == 201, response.data
    assert response.data["opportunity"]["status"] == "pursuing"
    latest = response.data["opportunity"]["latest_review"]
    assert latest["decision"] == "approve" and latest["reason"] == ""
    assert latest["reviewer"]["email"] == member.email
    assert latest["status_before"] == "new"

    stored = OpportunityReview.objects.get()
    assert stored.match_score == 78
    assert (stored.judge_prompt, stored.judge_model) == ("judge/v1", "openai/gpt-oss-120b")
    assert stored.snapshot["title"] == opportunity.title
    assert stored.snapshot["deadline"] == opportunity.deadline.isoformat()
    assert approvals == [
        {
            "signal": opportunity_approved,
            "organization_id": organization.pk,
            "opportunity_id": opportunity.pk,
            "review_id": stored.pk,
        }
    ]
    assert organization.events.filter(action="opportunity.approved").exists()


def test_reject_needs_a_reason_and_a_comment(auth_client, organization, opportunity, approvals):
    response = review(auth_client, organization, opportunity, decision="reject")
    assert response.status_code == 400
    assert set(response.data) == {"reason", "comment"}
    response = review(
        auth_client,
        organization,
        opportunity,
        decision="reject",
        reason="wrong_location",
        comment="   ",
    )
    assert set(response.data) == {"comment"}
    assert (
        review(
            auth_client, organization, opportunity, decision="reject", reason="made_up", comment="x"
        ).status_code
        == 400
    )
    assert not OpportunityReview.objects.exists()

    response = review(
        auth_client,
        organization,
        opportunity,
        decision="reject",
        reason="wrong_location",
        comment="  We only work in East Africa  ",
    )
    assert response.status_code == 201, response.data
    assert response.data["opportunity"]["status"] == "dismissed"
    assert response.data["review"]["comment"] == "We only work in East Africa"
    assert approvals == []
    event = organization.events.get(action="opportunity.rejected")
    assert event.metadata["reason"] == "wrong_location"


def test_approval_ignores_a_reason(auth_client, organization, opportunity):
    response = review(auth_client, organization, opportunity, decision="approve", reason="funder")
    assert response.status_code == 201
    assert response.data["review"]["reason"] == ""


def test_changing_your_mind_keeps_the_history(auth_client, organization, opportunity, approvals):
    review(
        auth_client,
        organization,
        opportunity,
        decision="reject",
        reason="capacity",
        comment="Busy until March",
    )
    response = review(auth_client, organization, opportunity, decision="approve")
    assert response.status_code == 201
    assert response.data["opportunity"]["status"] == "pursuing"
    assert response.data["review"]["status_before"] == "dismissed"
    history = auth_client.get(url("reviews", organization, opportunity_id=opportunity.pk))
    assert [row["decision"] for row in history.data] == ["approve", "reject"]
    assert len(approvals) == 1


def test_the_same_decision_twice_is_a_conflict(auth_client, organization, opportunity):
    assert review(auth_client, organization, opportunity, decision="approve").status_code == 201
    response = review(auth_client, organization, opportunity, decision="approve")
    assert response.status_code == 409
    assert response.data["code"] == "already_reviewed"
    tender = make_opportunity(organization, title="Road tender")
    body = {"decision": "reject", "reason": "wrong_focus", "comment": "No roads"}
    assert review(auth_client, organization, tender, **body).status_code == 201
    assert review(auth_client, organization, tender, **body).status_code == 409
    # A different reason corrects the rejection.
    body.update(reason="capacity", comment="Actually, no capacity")
    assert review(auth_client, organization, tender, **body).status_code == 201


def test_submitted_opportunities_are_past_review(auth_client, organization, opportunity):
    opportunity.status = "submitted"
    opportunity.save()
    response = review(
        auth_client,
        organization,
        opportunity,
        decision="reject",
        reason="capacity",
        comment="Too late",
    )
    assert response.status_code == 409
    assert response.data["code"] == "not_reviewable"


def test_review_access(auth_client, user, organization, opportunity):
    outsider = as_user(UserFactory())
    assert review(outsider, organization, opportunity, decision="approve").status_code == 404
    path = url("reviews", organization, opportunity_id=opportunity.pk)
    assert outsider.get(path).status_code == 404
    other = organization_services.create_organization(actor=user, name="Other")
    assert review(auth_client, other, opportunity, decision="approve").status_code == 404
    organization_services.archive_organization(organization_id=organization.pk, actor=user)
    assert review(auth_client, organization, opportunity, decision="approve").status_code == 400


def test_patch_cannot_approve_or_reject(auth_client, organization, opportunity):
    detail = url("detail", organization, opportunity_id=opportunity.pk)
    response = auth_client.patch(detail, {"status": "dismissed"}, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "use_review"
    assert auth_client.patch(detail, {"status": "reviewing"}, format="json").status_code == 200
    review(auth_client, organization, opportunity, decision="approve")
    # Later pipeline steps still use PATCH.
    response = auth_client.patch(detail, {"status": "submitted"}, format="json")
    assert response.status_code == 200 and response.data["status"] == "submitted"


def test_rejection_reasons_need_a_reason_in_the_database(organization, opportunity, user):
    with pytest.raises(IntegrityError), transaction.atomic():
        OpportunityReview.objects.create(
            organization=organization,
            opportunity=opportunity,
            reviewer=user,
            decision="reject",
            reason="",
            status_before="new",
            match_score=50,
        )


def test_rejection_reason_list(auth_client, organization):
    response = auth_client.get(url("rejection-reasons", organization))
    assert response.status_code == 200
    assert {"value": "wrong_location", "label": "Wrong country or region"} in response.data


# ---------------------------------------------------------------------------
# Feed filter
# ---------------------------------------------------------------------------


def test_feed_filters_by_review_state(auth_client, organization, opportunity):
    rejected = make_opportunity(organization, title="Mining Grant")
    pending = make_opportunity(organization, title="Pending Grant")
    review(auth_client, organization, opportunity, decision="approve")
    review(
        auth_client,
        organization,
        rejected,
        decision="reject",
        reason="wrong_focus",
        comment="Not our sector",
    )

    def titles(state):
        response = auth_client.get(url("list", organization), {"review": state})
        assert response.status_code == 200, response.data
        return [row["title"] for row in response.data["results"]]

    assert titles("approved") == [opportunity.title]
    assert titles("rejected") == ["Mining Grant"]
    assert titles("pending") == [pending.title]
    assert auth_client.get(url("list", organization), {"review": "maybe"}).status_code == 400
    listed = auth_client.get(url("list", organization)).data["results"]
    assert {row["title"]: bool(row["latest_review"]) for row in listed} == {
        opportunity.title: True,
        "Mining Grant": True,
        "Pending Grant": False,
    }


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


def test_insights(auth_client, user, organization, opportunity):
    agent = opportunity.agent
    for status in ("succeeded", "partial", "failed"):
        DiscoveryRun.objects.create(
            organization=organization,
            agent=agent,
            agent_name="a",
            trigger="manual",
            status=status,
        )
    old = make_opportunity(organization, title="Old", match_score=95, source_name="old.org")
    old.found_at = timezone.now() - timedelta(days=200)
    old.save()
    tender = make_opportunity(
        organization,
        title="Road tender",
        opportunity_type="tender",
        match_score=40,
        source_name="worldbank.org",
    )
    make_opportunity(organization, title="Unreviewed", match_score=60)
    review(auth_client, organization, opportunity, decision="approve")
    review(
        auth_client,
        organization,
        tender,
        decision="reject",
        reason="wrong_focus",
        comment="No roads",
    )
    review(
        auth_client,
        organization,
        tender,
        decision="reject",
        reason="capacity",
        comment="Changed reason",
    )

    data = auth_client.get(url("insights", organization), {"days": 90}).data
    assert data["days"] == 90
    assert sum(week["runs"] for week in data["runs_by_week"]) == 4  # incl. the fixture's run
    assert sum(week["failed"] for week in data["runs_by_week"]) == 1
    assert data["opportunities_found"] == 3  # "Old" is outside the window
    assert {row["key"]: row["count"] for row in data["by_type"]} == {"grant": 2, "tender": 1}
    assert {"key": "worldbank.org", "count": 1} in data["by_source"]
    buckets = {bucket["label"]: bucket["count"] for bucket in data["by_score"]}
    assert (buckets["40–54"], buckets["55–69"], buckets["70–84"]) == (1, 1, 1)
    # The tender counts once, by its latest review.
    assert data["reviews"] == {"approved": 1, "rejected": 1, "pending": 1, "approval_rate": 0.5}
    assert data["rejection_reasons"] == [{"key": "capacity", "count": 1}]

    assert auth_client.get(url("insights", organization), {"days": 3}).status_code == 400
    assert as_user(UserFactory()).get(url("insights", organization)).status_code == 404


def test_insights_for_a_new_organization(auth_client, organization):
    data = auth_client.get(url("insights", organization)).data
    assert data["runs_by_week"] == [] and data["opportunities_found"] == 0
    assert data["reviews"]["approval_rate"] is None
