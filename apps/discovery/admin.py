from django.contrib import admin

from .models import DiscoveryRun, RunCandidate, RunEvent, SearchAgent


@admin.register(SearchAgent)
class SearchAgentAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "schedule", "enabled", "created_at"]
    list_filter = ["schedule", "enabled"]
    search_fields = ["name", "organization__name"]


class RunEventInline(admin.TabularInline):
    model = RunEvent
    fields = ["seq", "step", "level", "message", "tool", "source", "at"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(DiscoveryRun)
class DiscoveryRunAdmin(admin.ModelAdmin):
    list_display = ["agent_name", "organization", "status", "runner", "found", "created_at"]
    list_filter = ["status", "runner", "trigger"]
    search_fields = ["agent_name", "organization__name"]
    readonly_fields = [field.name for field in DiscoveryRun._meta.fields]
    inlines = [RunEventInline]


@admin.register(RunCandidate)
class RunCandidateAdmin(admin.ModelAdmin):
    list_display = ["title", "run", "match_score", "decision"]
    list_filter = ["decision"]
