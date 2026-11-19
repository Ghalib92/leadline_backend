from django.urls import include, path

from apps.common.api.health import HealthView
from apps.common.api.tickets import TransferTicketView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("transfers/ticket/", TransferTicketView.as_view(), name="transfer-ticket"),
    path("auth/", include("apps.authentication.api.urls")),
    path("organizations/", include("apps.organizations.api.urls")),
    # These apps' endpoints live under each organization's URL.
    path("organizations/", include("apps.knowledge.api.urls")),
    path("organizations/", include("apps.discovery.api.urls")),
    path("organizations/", include("apps.opportunities.api.urls")),
    path("organizations/", include("apps.proposals.api.urls")),
    path("organizations/", include("apps.learning.api.urls")),
    path("organizations/", include("apps.outreach.api.urls")),
]
