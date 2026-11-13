from django.dispatch import receiver

from apps.opportunities.signals import opportunity_approved

from . import services


@receiver(opportunity_approved, dispatch_uid="proposals.draft_on_approval")
def draft_on_approval(sender, *, organization_id, opportunity_id, **kwargs):
    services.start_draft_for_approval(
        organization_id=organization_id, opportunity_id=opportunity_id
    )
