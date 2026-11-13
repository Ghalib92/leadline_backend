"""React to organization changes without apps.organizations importing this app."""

from django.dispatch import receiver

from apps.organizations.signals import context_changed, document_uploaded

from . import services


@receiver(document_uploaded, dispatch_uid="knowledge.process_uploaded_document")
def process_uploaded_document(sender, *, document_id, **kwargs):
    services.schedule_processing(document_id)


@receiver(context_changed, dispatch_uid="knowledge.refresh_dossier")
def refresh_dossier_on_change(sender, *, organization_id, **kwargs):
    # Cheap when nothing relevant changed: the job compares an input hash first.
    services.schedule_dossier_refresh(organization_id)
