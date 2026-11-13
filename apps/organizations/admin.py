from django.contrib import admin

from .models import (
    KnowledgeFolder,
    OpportunityPreferences,
    Organization,
    OrganizationDocument,
    OrganizationEvent,
    OrganizationInvitation,
    OrganizationLink,
    OrganizationMembership,
    OrgSource,
)


class OrganizationReadOnlyAdmin(admin.ModelAdmin):
    """Inspect records here; use service-backed APIs for writes and last-admin safeguards."""

    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(Organization)
class OrganizationAdmin(OrganizationReadOnlyAdmin):
    list_display = ["name", "country", "created_by", "archived_at", "created_at"]
    search_fields = ["name", "slug"]
    list_filter = ["country", "archived_at"]


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(OrganizationReadOnlyAdmin):
    list_display = ["organization", "user", "role", "status", "joined_at"]
    list_filter = ["role", "status"]
    search_fields = ["organization__name", "user__email"]
    list_select_related = ["organization", "user"]


@admin.register(OrganizationInvitation)
class OrganizationInvitationAdmin(OrganizationReadOnlyAdmin):
    exclude = ["token_hash"]
    list_display = [
        "organization",
        "email",
        "role",
        "effective_status",
        "email_status",
        "expires_at",
    ]
    list_filter = ["status", "email_status", "role"]
    search_fields = ["organization__name", "email"]
    list_select_related = ["organization"]

    def get_readonly_fields(self, request, obj=None):
        return [name for name in super().get_readonly_fields(request, obj) if name != "token_hash"]


@admin.register(OrganizationEvent)
class OrganizationEventAdmin(OrganizationReadOnlyAdmin):
    list_display = ["organization", "actor", "action", "created_at"]
    list_filter = ["action"]
    search_fields = ["organization__name", "actor__email"]
    list_select_related = ["organization", "actor"]


@admin.register(OpportunityPreferences)
class OpportunityPreferencesAdmin(OrganizationReadOnlyAdmin):
    list_display = ["organization", "funding_currency", "updated_at"]
    search_fields = ["organization__name"]
    list_select_related = ["organization"]


@admin.register(OrganizationLink)
class OrganizationLinkAdmin(OrganizationReadOnlyAdmin):
    list_display = ["organization", "kind", "url"]
    list_filter = ["kind"]
    search_fields = ["organization__name", "url"]
    list_select_related = ["organization"]


@admin.register(KnowledgeFolder)
class KnowledgeFolderAdmin(OrganizationReadOnlyAdmin):
    list_display = ["organization", "name", "purpose", "created_at"]
    search_fields = ["organization__name", "name"]
    list_select_related = ["organization"]


@admin.register(OrganizationDocument)
class OrganizationDocumentAdmin(OrganizationReadOnlyAdmin):
    list_display = [
        "organization", "folder", "title", "category", "size", "processing_status", "created_at"
    ]
    list_filter = ["category", "processing_status"]
    search_fields = ["organization__name", "title"]
    list_select_related = ["organization"]


@admin.register(OrgSource)
class OrgSourceAdmin(OrganizationReadOnlyAdmin):
    list_display = ["organization", "label", "url", "enabled", "created_at"]
    list_filter = ["enabled"]
    search_fields = ["organization__name", "label", "url"]
    list_select_related = ["organization"]

