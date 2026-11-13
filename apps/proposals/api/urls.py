from django.urls import path

from . import views

# The "shortuuid" converter is registered by apps.organizations.api.urls.
app_name = "proposals"

P = "<shortuuid:organization_id>/proposals/<shortuuid:proposal_id>"

urlpatterns = [
    path("<shortuuid:organization_id>/proposals/", views.ProposalListView.as_view(), name="list"),
    path(
        "<shortuuid:organization_id>/opportunities/<shortuuid:opportunity_id>/proposal/",
        views.OpportunityProposalView.as_view(),
        name="for-opportunity",
    ),
    path(f"{P}/", views.ProposalDetailView.as_view(), name="detail"),
    path(f"{P}/events/", views.ProposalEventsView.as_view(), name="events"),
    path(f"{P}/versions/", views.VersionListView.as_view(), name="versions"),
    path(f"{P}/versions/<int:number>/", views.VersionDetailView.as_view(), name="version"),
    path(
        f"{P}/versions/<int:number>/restore/",
        views.VersionRestoreView.as_view(),
        name="version-restore",
    ),
    path(f"{P}/sections/<slug:key>/", views.SectionEditView.as_view(), name="section"),
    path(
        f"{P}/sections/<slug:key>/rewrite/",
        views.SectionRewriteView.as_view(),
        name="section-rewrite",
    ),
    path(f"{P}/export/", views.ProposalExportView.as_view(), name="export"),
]
