import pytest
from django.core import mail

from apps.authentication.tests.factories import UserFactory

from ..models import Organization, OrganizationInvitation
from .test_api import as_user, url

pytestmark = pytest.mark.django_db(transaction=True)


def test_create_organization_with_invitations(auth_client, user):
    response = auth_client.post(
        url("list"),
        {
            "name": "Acme Africa",
            "invitations": [
                {"email": "Amina@Example.com", "role": "admin", "message": "Join us"},
                {"email": "otieno@example.com"},
            ],
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["my_membership"]["role"] == "admin"
    invited = {(item["email"], item["role"]) for item in response.data["invitations"]}
    assert invited == {("amina@example.com", "admin"), ("otieno@example.com", "member")}
    assert {item["status"] for item in response.data["invitations"]} == {"pending"}
    recipients = {message.to[0] for message in mail.outbox}
    assert {"amina@example.com", "otieno@example.com", user.email} <= recipients


def test_create_organization_without_invitations_still_works(auth_client):
    response = auth_client.post(url("list"), {"name": "Solo"}, format="json")
    assert response.status_code == 201
    assert response.data["invitations"] == []


@pytest.mark.parametrize(
    "invitations",
    [
        [{"email": "a@example.com"}, {"email": "A@EXAMPLE.com"}],
        [{"email": "not-an-email"}],
        [{"email": "a@example.com", "role": "owner"}],
        [{"email": f"user{i}@example.com"} for i in range(21)],
    ],
)
def test_invalid_creation_invitations_are_rejected(auth_client, invitations):
    response = auth_client.post(
        url("list"), {"name": "Org", "invitations": invitations}, format="json"
    )
    assert response.status_code == 400
    assert "invitations" in response.data
    assert not Organization.objects.exists()


def test_inviting_yourself_rolls_back_the_whole_organization(auth_client, user):
    mail.outbox.clear()
    response = auth_client.post(
        url("list"),
        {"name": "Org", "invitations": [{"email": "ok@example.com"}, {"email": user.email}]},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["invitations"][0] == {}
    assert "email" in response.data["invitations"][1]
    assert not Organization.objects.exists()
    assert not OrganizationInvitation.objects.exists()
    assert mail.outbox == []


def test_admin_bulk_invites(organization, user):
    response = as_user(user).post(
        url("invitations-bulk", organization),
        {"invitations": [{"email": "one@example.com"}, {"email": "two@example.com"}]},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert [item["email"] for item in response.data] == ["one@example.com", "two@example.com"]
    assert organization.invitations.filter(status="pending").count() == 2
    assert len(mail.outbox) == 2


def test_bulk_invite_is_all_or_nothing(organization, user):
    client = as_user(user)
    client.post(url("invitations", organization), {"email": "taken@example.com"}, format="json")
    mail.outbox.clear()
    response = client.post(
        url("invitations-bulk", organization),
        {"invitations": [{"email": "new@example.com"}, {"email": "taken@example.com"}]},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["invitations"][0] == {}
    assert "email" in response.data["invitations"][1]
    assert not organization.invitations.filter(email="new@example.com").exists()
    assert mail.outbox == []


def test_bulk_invite_requires_admin(organization, membership):
    response = as_user(membership.user).post(
        url("invitations-bulk", organization),
        {"invitations": [{"email": "x@example.com"}]},
        format="json",
    )
    assert response.status_code == 403


def test_bulk_invite_rejects_empty_list(organization, user):
    response = as_user(user).post(
        url("invitations-bulk", organization), {"invitations": []}, format="json"
    )
    assert response.status_code == 400


def test_outsider_cannot_bulk_invite(organization):
    response = as_user(UserFactory()).post(
        url("invitations-bulk", organization),
        {"invitations": [{"email": "x@example.com"}]},
        format="json",
    )
    assert response.status_code in {403, 404}


def received(client, **params):
    return client.get(url("received-invitations"), params)


def test_recipient_lists_pending_invitation_and_accepts_by_id(organization, user):
    recipient = UserFactory()
    as_user(user).post(url("invitations", organization), {"email": recipient.email}, format="json")
    client = as_user(recipient)

    listed = received(client).data["results"]
    assert [(item["organization_name"], item["status"]) for item in listed] == [
        (organization.name, "pending")
    ]

    accept_url = url("received-invitation-accept", invitation_id=listed[0]["id"])
    response = client.post(accept_url)
    assert response.status_code == 200, response.data
    assert organization.memberships.filter(user=recipient, status="active").exists()

    # Already accepted (for example on sign-in): shown as accepted, and accepting again is a no-op.
    assert received(client).data["results"][0]["status"] == "accepted"
    assert received(client, status="pending").data["results"] == []
    assert client.post(accept_url).status_code == 200
    assert organization.memberships.filter(user=recipient).count() == 1


def test_recipient_declines_by_id(organization, user):
    recipient = UserFactory()
    as_user(user).post(url("invitations", organization), {"email": recipient.email}, format="json")
    client = as_user(recipient)
    invitation_id = received(client).data["results"][0]["id"]
    response = client.post(url("received-invitation-decline", invitation_id=invitation_id))
    assert response.status_code == 204
    assert received(client).data["results"][0]["status"] == "declined"
    accept = client.post(url("received-invitation-accept", invitation_id=invitation_id))
    assert accept.status_code == 400
    assert not organization.memberships.filter(user=recipient).exists()


def test_others_cannot_see_or_answer_someone_elses_invitation(organization, user):
    recipient, stranger = UserFactory(), UserFactory()
    as_user(user).post(url("invitations", organization), {"email": recipient.email}, format="json")
    invitation_id = received(as_user(recipient)).data["results"][0]["id"]
    client = as_user(stranger)
    assert received(client).data["results"] == []
    accept = client.post(url("received-invitation-accept", invitation_id=invitation_id))
    assert accept.status_code == 400
    assert not organization.memberships.filter(user=stranger).exists()


def test_revoked_invitations_are_hidden_from_recipient(organization, user):
    recipient = UserFactory()
    response = as_user(user).post(
        url("invitations", organization), {"email": recipient.email}, format="json"
    )
    as_user(user).post(
        url("invitation-revoke", organization, invitation_id=response.data["id"])
    )
    assert received(as_user(recipient)).data["results"] == []
