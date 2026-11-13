from django.dispatch import receiver

from apps.opportunities.choices import ReviewDecision
from apps.opportunities.signals import opportunity_reviewed


@receiver(opportunity_reviewed, dispatch_uid="learning.refresh_suggestions")
def look_for_patterns(sender, *, organization_id, decision, **kwargs):
    if decision != ReviewDecision.REJECT:
        return
    from apps.common.jobs import enqueue

    from .tasks import refresh_suggestions

    enqueue(refresh_suggestions, organization_id)  # the signal is already sent on commit
