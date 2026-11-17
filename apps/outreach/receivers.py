from django.dispatch import receiver

from apps.opportunities.signals import opportunity_approved

from . import services


@receiver(opportunity_approved, dispatch_uid="outreach.pitch_on_approval")
def pitch_on_approval(sender, *, organization_id, opportunity_id, **kwargs):
    """An approved client lead gets its pitch written (the Proposal Writer skips them)."""
    services.write_pitch_for_approval(
        organization_id=organization_id, opportunity_id=opportunity_id
    )
