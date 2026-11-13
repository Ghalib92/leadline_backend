from django.dispatch import receiver

from .signals import opportunity_reviewed


@receiver(opportunity_reviewed, dispatch_uid="opportunities.embed_review")
def remember_review(sender, *, review_id, **kwargs):
    from apps.common.jobs import enqueue

    from .tasks import embed_review

    enqueue(embed_review, review_id)  # already after commit (the signal is sent on commit)
