from django.urls import path

from . import views

# The "shortuuid" converter is registered by apps.organizations.api.urls.
app_name = "discovery"

urlpatterns = [
    path("<shortuuid:organization_id>/agents/", views.AgentListCreateView.as_view(), name="agents"),
    path(
        "<shortuuid:organization_id>/agents/<shortuuid:agent_id>/",
        views.AgentDetailView.as_view(),
        name="agent-detail",
    ),
    path(
        "<shortuuid:organization_id>/agents/<shortuuid:agent_id>/run/",
        views.AgentRunView.as_view(),
        name="agent-run",
    ),
    path(
        "<shortuuid:organization_id>/agent-sources/",
        views.SourceCategoryListView.as_view(),
        name="sources",
    ),
    path(
        "<shortuuid:organization_id>/run-settings/",
        views.RunSettingsView.as_view(),
        name="run-settings",
    ),
    path("<shortuuid:organization_id>/runs/", views.RunListView.as_view(), name="runs"),
    path(
        "<shortuuid:organization_id>/runs/<shortuuid:run_id>/",
        views.RunDetailView.as_view(),
        name="run-detail",
    ),
    path(
        "<shortuuid:organization_id>/runs/<shortuuid:run_id>/events/",
        views.RunEventsView.as_view(),
        name="run-events",
    ),
]
