from django.contrib import admin

from .models import EvalRun, PreferenceSuggestion


@admin.register(PreferenceSuggestion)
class PreferenceSuggestionAdmin(admin.ModelAdmin):
    list_display = ["organization", "kind", "value", "status", "created_at"]
    list_filter = ["kind", "status"]


@admin.register(EvalRun)
class EvalRunAdmin(admin.ModelAdmin):
    list_display = ["suite", "dataset", "label", "cases", "errors", "created_at"]
    list_filter = ["suite"]
    readonly_fields = [field.name for field in EvalRun._meta.fields]
