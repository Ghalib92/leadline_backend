from django.contrib import admin

from .models import Proposal, ProposalVersion


class VersionInline(admin.TabularInline):
    model = ProposalVersion
    fields = ["number", "source", "note", "created_by", "created_at"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = ["opportunity", "organization", "status", "version", "runner", "updated_at"]
    list_filter = ["status", "runner"]
    search_fields = ["opportunity__title", "organization__name"]
    readonly_fields = [field.name for field in Proposal._meta.fields]
    inlines = [VersionInline]
