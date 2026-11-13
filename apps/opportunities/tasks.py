import logging

from apps.common.jobs import job

from .memory import memory_text
from .models import Opportunity, OpportunityReview

logger = logging.getLogger(__name__)


@job("opportunities.embed_review")
def embed_review(review_id):
    """Add a review to decision memory."""
    from apps.knowledge.embeddings import get_embedder

    review = OpportunityReview.objects.filter(pk=review_id, embedding__isnull=True).first()
    if review is None:
        return
    vector = get_embedder().embed_documents([memory_text(review.snapshot)])[0]
    OpportunityReview.objects.filter(pk=review_id).update(embedding=vector)


@job("opportunities.analyze_opportunity")
def analyze_opportunity(opportunity_id):
    """Run opportunity_analyst in the background to generate an executive brief."""
    from .services import generate_brief_for_opportunity

    opportunity = Opportunity.objects.filter(pk=opportunity_id).first()
    if opportunity is None or "brief" in (opportunity.requirements or {}):
        return
    try:
        generate_brief_for_opportunity(opportunity)
    except Exception:
        logger.exception("Failed to generate brief for opportunity %s", opportunity_id)
        # Recorded so the page can offer a retry instead of waiting forever.
        opportunity.requirements = {
            **(opportunity.requirements or {}),
            "brief_error": "The brief could not be prepared.",
        }
        opportunity.save(update_fields=["requirements", "updated_at"])

