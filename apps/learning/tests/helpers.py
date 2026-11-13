from django.urls import reverse

from apps.discovery.tests.factories import make_opportunity


def review(
    client,
    organization,
    *,
    title,
    decision="reject",
    reason="",
    comment="Not for us",
    **opportunity,
):
    """Create an opportunity and review it through the API."""
    item = make_opportunity(organization, title=title, **opportunity)
    body = {"decision": decision, "comment": comment}
    if decision == "reject":
        body["reason"] = reason
    path = reverse(
        "v1:opportunities:reviews",
        kwargs={"organization_id": organization.pk, "opportunity_id": item.pk},
    )
    response = client.post(path, body, format="json")
    assert response.status_code == 201, response.data
    return item
