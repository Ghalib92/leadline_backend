"""Transfer tickets: the browser uploads and downloads documents straight from the API
(the UI's host caps a request at 4.5 MB), with a ticket the signed-in UI server asked
for. A ticket is good for one path and method, for a few minutes, and the view still
checks membership."""

import pytest
from django.core import signing
from django.urls import reverse
from rest_framework.test import APIClient

from apps.authentication.tests.factories import UserFactory
from apps.common.api.tickets import SALT, make_ticket

from .test_profile import as_user, upload, url

pytestmark = pytest.mark.django_db(transaction=True)

TICKET_URL = reverse("v1:transfer-ticket")


def ticket_for(client, path, method="POST"):
    response = client.post(TICKET_URL, {"path": path, "method": method}, format="json")
    assert response.status_code == 200, response.data
    return response.data


def test_the_browser_uploads_straight_to_the_api_with_a_ticket(auth_client, organization, user):
    path = url("documents", organization)
    granted = ticket_for(auth_client, path)
    assert granted["url"] == f"http://testserver{path}"

    browser = APIClient()  # no session, no token
    browser.credentials(HTTP_AUTHORIZATION=f"Ticket {granted['ticket']}")
    response = upload(browser, organization)

    assert response.status_code == 201, response.data
    assert organization.documents.get().uploaded_by == user


def test_the_browser_downloads_with_a_ticket_in_the_link(auth_client, organization):
    document = upload(auth_client, organization).data
    path = url("document-download", organization, document_id=document["id"])
    granted = ticket_for(auth_client, path, "GET")

    response = APIClient().get(granted["url"], {"ticket": granted["ticket"]})

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"%PDF-1.4 example"


def test_a_ticket_is_only_good_for_its_own_path_and_method(auth_client, organization, user):
    document = upload(auth_client, organization).data
    download = url("document-download", organization, document_id=document["id"])
    ticket = make_ticket(user, path=download, method="GET")
    browser = APIClient()

    # Another path (the document list), and the same path with another method.
    assert browser.get(url("documents", organization), {"ticket": ticket}).status_code == 401
    browser.credentials(HTTP_AUTHORIZATION=f"Ticket {ticket}")
    assert browser.post(download).status_code == 401


def test_an_expired_or_forged_ticket_is_refused(auth_client, organization, user, settings):
    path = url("documents", organization)
    browser = APIClient()

    settings.TRANSFER_TICKET_SECONDS = -1
    browser.credentials(HTTP_AUTHORIZATION=f"Ticket {make_ticket(user, path=path, method='POST')}")
    expired = upload(browser, organization)
    assert expired.status_code == 401
    assert "expired" in str(expired.data["detail"])

    settings.TRANSFER_TICKET_SECONDS = 600
    forged = signing.dumps({"u": str(user.pk), "p": path, "m": "POST"}, salt=SALT + "x")
    browser.credentials(HTTP_AUTHORIZATION=f"Ticket {forged}")
    assert upload(browser, organization).status_code == 401


def test_a_ticket_still_needs_the_right_membership(organization):
    outsider = UserFactory()
    path = url("documents", organization)
    granted = ticket_for(as_user(outsider), path)

    browser = APIClient()
    browser.credentials(HTTP_AUTHORIZATION=f"Ticket {granted['ticket']}")

    assert upload(browser, organization).status_code in (403, 404)
    assert not organization.documents.exists()


def test_a_path_can_be_given_as_the_ui_names_it(auth_client, organization):
    """The UI calls `/api/organizations/<id>/documents`; its proxy adds the API root."""
    granted = ticket_for(auth_client, f"organizations/{organization.pk}/documents")

    assert granted["url"] == f"http://testserver{url('documents', organization)}"


def test_tickets_are_only_given_for_file_transfers(auth_client, organization):
    # A path whose view doesn't take tickets (the organization itself), or no view at all.
    documents = url("documents", organization)
    for path in (
        url("detail", organization),
        "/api/v1/nowhere/",
        "documents",
        f"organizations/{organization.pk}/../{organization.pk}/documents",
        documents.replace("/api/v1/", "/api/v1/../v1/"),
    ):
        response = auth_client.post(TICKET_URL, {"path": path, "method": "POST"}, format="json")
        assert response.status_code == 400, path

    signed_out = APIClient().post(
        TICKET_URL, {"path": url("documents", organization), "method": "POST"}, format="json"
    )
    assert signed_out.status_code == 401
