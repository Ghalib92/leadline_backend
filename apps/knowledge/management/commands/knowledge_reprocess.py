from django.core.management.base import BaseCommand

from apps.common.jobs import enqueue
from apps.organizations.models import OrganizationDocument

from ...tasks import process_document


class Command(BaseCommand):
    help = (
        "Queue documents for text extraction and embedding again, e.g. after changing the "
        "embedding model. Needs a running Temporal worker unless --now is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization", help="Only this organization's documents (id).")
        parser.add_argument(
            "--status",
            action="append",
            dest="statuses",
            help="Only documents in this processing status (repeatable). Default: all.",
        )
        parser.add_argument(
            "--now", action="store_true", help="Process in this process instead of queueing."
        )

    def handle(self, *args, organization=None, statuses=None, now=False, **options):
        documents = OrganizationDocument.objects.order_by("created_at")
        if organization:
            documents = documents.filter(organization_id=organization)
        if statuses:
            documents = documents.filter(processing_status__in=statuses)
        count = 0
        for document_id in documents.values_list("pk", flat=True).iterator():
            if now:
                process_document(document_id)
            else:
                enqueue(process_document, document_id)
            count += 1
        verb = "Processed" if now else "Queued"
        self.stdout.write(self.style.SUCCESS(f"{verb} {count} document(s)."))
