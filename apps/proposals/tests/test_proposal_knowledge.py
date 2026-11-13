"""A proposal can choose which company documents its evidence comes from; until it
does, it follows the run that found the opportunity."""

import pytest

from apps.discovery.models import DiscoveryRun
from apps.discovery.tests.factories import make_agent, make_opportunity
from apps.organizations.tests.factories import make_folder
from apps.proposals import selectors

from .test_proposals import approve, detail, url

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def proposal(auth_client, user, organization):
    folder = make_folder(organization, "Run folder")
    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        config={"knowledge": {"mode": "folders", "folder_ids": [folder.pk]}},
    )
    opportunity = make_opportunity(organization, title="Youth Data Fund", run=run)
    approve(auth_client, organization, opportunity)
    return opportunity.proposal


def documents(auth_client, organization, proposal):
    return detail(auth_client, organization, proposal).data["knowledge"]


def choose(auth_client, organization, proposal, **body):
    return auth_client.patch(
        url("detail", organization, proposal_id=proposal.pk), body, format="json"
    )


def test_until_chosen_it_follows_the_run(auth_client, organization, proposal):
    knowledge = documents(auth_client, organization, proposal)
    assert knowledge["source"] == "run"
    assert knowledge["mode"] == "folders"
    assert [f["name"] for f in knowledge["folders"]] == ["Run folder"]


def test_choosing_folders_changes_where_evidence_comes_from(auth_client, organization, proposal):
    own = make_folder(organization, "Past proposals")
    response = choose(
        auth_client, organization, proposal, knowledge_mode="selected", folders=[own.pk]
    )
    assert response.status_code == 200, response.data
    assert response.data["knowledge"] == {
        "source": "proposal",
        "mode": "folders",
        "folders": [{"id": own.pk, "name": "Past proposals"}],
    }
    proposal.refresh_from_db()
    assert selectors.writer_inputs(proposal)["knowledge"] == {
        "mode": "folders",
        "folder_ids": [own.pk],
    }


def test_none_and_back_to_the_run(auth_client, organization, proposal):
    choose(auth_client, organization, proposal, knowledge_mode="none")
    proposal.refresh_from_db()
    assert selectors.writer_inputs(proposal)["knowledge"]["mode"] == "none"
    choose(auth_client, organization, proposal, knowledge_mode="run")
    assert documents(auth_client, organization, proposal)["source"] == "run"


@pytest.mark.parametrize(
    "body",
    [
        {"knowledge_mode": "selected", "folders": []},
        {"knowledge_mode": "selected", "folders": ["nope12345678"]},
        {"knowledge_mode": "sometimes"},
    ],
)
def test_bad_choices_are_rejected(auth_client, organization, proposal, body):
    assert choose(auth_client, organization, proposal, **body).status_code == 400
