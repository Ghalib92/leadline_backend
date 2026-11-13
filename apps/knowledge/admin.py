from django.contrib import admin

from .models import CompanyDossier, DocumentChunk


@admin.register(CompanyDossier)
class CompanyDossierAdmin(admin.ModelAdmin):
    list_display = ["organization", "status", "version", "model", "prompt_id", "generated_at"]
    list_filter = ["status"]
    search_fields = ["organization__name"]
    readonly_fields = [field.name for field in CompanyDossier._meta.fields]


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ["document", "position", "organization"]
    search_fields = ["document__title", "text"]
    exclude = ["embedding"]
    readonly_fields = ["organization", "document", "position", "text", "metadata"]
