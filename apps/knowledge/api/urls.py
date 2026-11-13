from django.urls import path

from . import views

# The "shortuuid" path converter is registered by apps.organizations.api.urls, which
# config/api_router.py includes first.
app_name = "knowledge"

urlpatterns = [
    path("<shortuuid:organization_id>/dossier/", views.DossierView.as_view(), name="dossier"),
    path(
        "<shortuuid:organization_id>/dossier/regenerate/",
        views.DossierRefreshView.as_view(),
        name="dossier-regenerate",
    ),
    path(
        "<shortuuid:organization_id>/documents/<shortuuid:document_id>/reprocess/",
        views.DocumentReprocessView.as_view(),
        name="document-reprocess",
    ),
]
