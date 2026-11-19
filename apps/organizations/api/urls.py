from django.urls import path, register_converter

from apps.common.api.converters import ShortUUIDConverter

from . import views

app_name = "organizations"

register_converter(ShortUUIDConverter, "shortuuid")

urlpatterns = [
    path("", views.OrganizationListCreateView.as_view(), name="list"),
    path("invitations/preview/", views.InvitationPreviewView.as_view(), name="invitation-preview"),
    path("invitations/accept/", views.InvitationAcceptView.as_view(), name="invitation-accept"),
    path("invitations/decline/", views.InvitationDeclineView.as_view(), name="invitation-decline"),
    path(
        "invitations/received/",
        views.ReceivedInvitationListView.as_view(),
        name="received-invitations",
    ),
    path(
        "invitations/received/<shortuuid:invitation_id>/accept/",
        views.ReceivedInvitationAcceptView.as_view(),
        name="received-invitation-accept",
    ),
    path(
        "invitations/received/<shortuuid:invitation_id>/decline/",
        views.ReceivedInvitationDeclineView.as_view(),
        name="received-invitation-decline",
    ),
    path("<shortuuid:organization_id>/", views.OrganizationDetailView.as_view(), name="detail"),
    path(
        "<shortuuid:organization_id>/archive/",
        views.OrganizationArchiveView.as_view(),
        name="archive",
    ),
    path(
        "<shortuuid:organization_id>/restore/",
        views.OrganizationRestoreView.as_view(),
        name="restore",
    ),
    path("<shortuuid:organization_id>/leave/", views.OrganizationLeaveView.as_view(), name="leave"),
    path(
        "<shortuuid:organization_id>/members/", views.MembershipListView.as_view(), name="members"
    ),
    path(
        "<shortuuid:organization_id>/members/<shortuuid:membership_id>/",
        views.MembershipDetailView.as_view(),
        name="member-detail",
    ),
    path(
        "<shortuuid:organization_id>/invitations/",
        views.InvitationListCreateView.as_view(),
        name="invitations",
    ),
    path(
        "<shortuuid:organization_id>/invitations/bulk/",
        views.InvitationBulkCreateView.as_view(),
        name="invitations-bulk",
    ),
    path(
        "<shortuuid:organization_id>/invitations/<shortuuid:invitation_id>/resend/",
        views.InvitationResendView.as_view(),
        name="invitation-resend",
    ),
    path(
        "<shortuuid:organization_id>/invitations/<shortuuid:invitation_id>/revoke/",
        views.InvitationRevokeView.as_view(),
        name="invitation-revoke",
    ),
    path("<shortuuid:organization_id>/activity/", views.EventListView.as_view(), name="activity"),
    path("<shortuuid:organization_id>/setup/", views.OrganizationSetupView.as_view(), name="setup"),
    path(
        "<shortuuid:organization_id>/preferences/",
        views.OpportunityPreferencesView.as_view(),
        name="preferences",
    ),
    path(
        "<shortuuid:organization_id>/client-brief/",
        views.ClientBriefView.as_view(),
        name="client-brief",
    ),
    path(
        "<shortuuid:organization_id>/job-brief/",
        views.JobBriefView.as_view(),
        name="job-brief",
    ),
    path(
        "<shortuuid:organization_id>/job-brief/read-cv/",
        views.JobBriefReadCvView.as_view(),
        name="job-brief-read-cv",
    ),
    path(
        "<shortuuid:organization_id>/job-brief/dismiss-suggestions/",
        views.JobBriefDismissSuggestionsView.as_view(),
        name="job-brief-dismiss-suggestions",
    ),
    path(
        "<shortuuid:organization_id>/tender-brief/",
        views.TenderBriefView.as_view(),
        name="tender-brief",
    ),
    path("<shortuuid:organization_id>/links/", views.LinkListCreateView.as_view(), name="links"),
    path(
        "<shortuuid:organization_id>/links/<shortuuid:link_id>/",
        views.LinkDetailView.as_view(),
        name="link-detail",
    ),
    path(
        "<shortuuid:organization_id>/folders/",
        views.FolderListCreateView.as_view(),
        name="folders",
    ),
    path(
        "<shortuuid:organization_id>/folders/<shortuuid:folder_id>/",
        views.FolderDetailView.as_view(),
        name="folder-detail",
    ),
    path(
        "<shortuuid:organization_id>/sources/",
        views.OrgSourceListCreateView.as_view(),
        name="sources",
    ),
    path(
        "<shortuuid:organization_id>/sources/<shortuuid:source_id>/",
        views.OrgSourceDetailView.as_view(),
        name="source-detail",
    ),
    path(
        "<shortuuid:organization_id>/documents/",
        views.DocumentListCreateView.as_view(),
        name="documents",
    ),
    path(
        "<shortuuid:organization_id>/documents/<shortuuid:document_id>/",
        views.DocumentDetailView.as_view(),
        name="document-detail",
    ),
    path(
        "<shortuuid:organization_id>/documents/<shortuuid:document_id>/download/",
        views.DocumentDownloadView.as_view(),
        name="document-download",
    ),
]
