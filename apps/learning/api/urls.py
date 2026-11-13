from django.urls import path

from . import views

# The "shortuuid" converter is registered by apps.organizations.api.urls.
app_name = "learning"

S = "<shortuuid:organization_id>/preference-suggestions"

urlpatterns = [
    path(f"{S}/", views.SuggestionListView.as_view(), name="suggestions"),
    path(
        f"{S}/<shortuuid:suggestion_id>/accept/",
        views.SuggestionAcceptView.as_view(),
        name="suggestion-accept",
    ),
    path(
        f"{S}/<shortuuid:suggestion_id>/dismiss/",
        views.SuggestionDismissView.as_view(),
        name="suggestion-dismiss",
    ),
]
