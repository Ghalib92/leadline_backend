from django.urls import path

from . import views

# The "shortuuid" converter is registered by apps.organizations.api.urls.
app_name = "outreach"

ORG = "<shortuuid:organization_id>"
OPPORTUNITY = f"{ORG}/opportunities/<shortuuid:opportunity_id>"
EMAIL = f"{ORG}/outreach/<shortuuid:email_id>"

urlpatterns = [
    path(f"{OPPORTUNITY}/contacts/", views.ContactListView.as_view(), name="contacts"),
    path(
        f"{OPPORTUNITY}/contact-search/", views.ContactSearchView.as_view(), name="contact-search"
    ),
    path(f"{OPPORTUNITY}/outreach/", views.OpportunityOutreachView.as_view(), name="draft"),
    path(
        f"{OPPORTUNITY}/calendar/",
        views.OpportunityCalendarView.as_view(),
        name="opportunity-calendar",
    ),
    path(
        f"{ORG}/contacts/<shortuuid:contact_id>/",
        views.ContactDetailView.as_view(),
        name="contact",
    ),
    path(f"{OPPORTUNITY}/pitch/", views.LeadPitchView.as_view(), name="pitch"),
    path(f"{OPPORTUNITY}/application/", views.JobApplicationView.as_view(), name="application"),
    path(
        f"{OPPORTUNITY}/application/export/",
        views.JobApplicationExportView.as_view(),
        name="application-export",
    ),
    path(f"{OPPORTUNITY}/activity/", views.LeadActivityListView.as_view(), name="activity"),
    path(
        f"{ORG}/lead-activity/<shortuuid:activity_id>/",
        views.LeadActivityDetailView.as_view(),
        name="activity-detail",
    ),
    path(f"{ORG}/call-list/", views.CallListAPIView.as_view(), name="call-list"),
    path(f"{ORG}/outreach/", views.OutreachListView.as_view(), name="list"),
    path(f"{EMAIL}/", views.OutreachDetailView.as_view(), name="detail"),
    path(f"{EMAIL}/redraft/", views.OutreachRedraftView.as_view(), name="redraft"),
    path(f"{EMAIL}/discard/", views.OutreachDiscardView.as_view(), name="discard"),
    path(f"{EMAIL}/approve/", views.OutreachApproveView.as_view(), name="approve"),
    path(f"{ORG}/outreach-purposes/", views.OutreachPurposeListView.as_view(), name="purposes"),
    path(f"{ORG}/deadlines/", views.DeadlineListView.as_view(), name="deadlines"),
    path(f"{ORG}/deadlines/calendar/", views.DeadlineCalendarView.as_view(), name="calendar"),
    path(f"{ORG}/reminders/", views.ReminderPreferenceView.as_view(), name="reminders"),
]
