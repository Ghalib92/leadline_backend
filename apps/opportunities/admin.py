from django.contrib import admin

from .models import Opportunity, OpportunityReview


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "organization",
        "opportunity_type",
        "match_score",
        "status",
        "deadline",
        "found_at",
    ]
    list_filter = ["status", "opportunity_type"]
    search_fields = ["title", "funder", "organization__name"]


@admin.register(OpportunityReview)
class OpportunityReviewAdmin(admin.ModelAdmin):
    list_display = ["opportunity", "decision", "reason", "reviewer", "match_score", "created_at"]
    list_filter = ["decision", "reason"]
    search_fields = ["opportunity__title", "comment"]
    readonly_fields = [field.name for field in OpportunityReview._meta.fields]
