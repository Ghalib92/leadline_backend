from django.urls import path

from . import views

# The "shortuuid" converter is registered by apps.organizations.api.urls.
app_name = "opportunities"

urlpatterns = [
    path(
        "<shortuuid:organization_id>/opportunities/",
        views.OpportunityListView.as_view(),
        name="list",
    ),
    path(
        "<shortuuid:organization_id>/opportunities/<shortuuid:opportunity_id>/",
        views.OpportunityDetailView.as_view(),
        name="detail",
    ),
    path(
        "<shortuuid:organization_id>/opportunities/<shortuuid:opportunity_id>/brief/",
        views.OpportunityBriefView.as_view(),
        name="brief",
    ),
    path(
        "<shortuuid:organization_id>/opportunities/<shortuuid:opportunity_id>/reviews/",
        views.OpportunityReviewView.as_view(),
        name="reviews",
    ),
    path(
        "<shortuuid:organization_id>/rejection-reasons/",
        views.RejectionReasonListView.as_view(),
        name="rejection-reasons",
    ),
    path("<shortuuid:organization_id>/overview/", views.OverviewView.as_view(), name="overview"),
    path("<shortuuid:organization_id>/insights/", views.InsightsView.as_view(), name="insights"),
]
