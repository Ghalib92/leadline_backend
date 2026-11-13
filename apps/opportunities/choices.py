from django.db import models


class OpportunityStatus(models.TextChoices):
    """The pipeline, matching the frontend: new → reviewing → pursuing → submitted →
    won/lost, or dismissed at any point."""

    NEW = "new", "New"
    REVIEWING = "reviewing", "Reviewing"
    PURSUING = "pursuing", "Pursuing"
    SUBMITTED = "submitted", "Submitted"
    WON = "won", "Won"
    LOST = "lost", "Lost"
    DISMISSED = "dismissed", "Dismissed"


CLOSED_STATUSES = (OpportunityStatus.WON, OpportunityStatus.LOST, OpportunityStatus.DISMISSED)
ACTIVE_PIPELINE_STATUSES = (OpportunityStatus.PURSUING, OpportunityStatus.SUBMITTED)


class ReviewDecision(models.TextChoices):
    APPROVE = "approve", "Approve"
    REJECT = "reject", "Reject"


class RejectionReason(models.TextChoices):
    """Why an opportunity was turned down. The learning loop counts these to suggest
    preference changes, so keep them about the fit, not about the person."""

    NOT_ELIGIBLE = "not_eligible", "We are not eligible"
    WRONG_FOCUS = "wrong_focus", "Not our focus area"
    WRONG_LOCATION = "wrong_location", "Wrong country or region"
    AMOUNT_TOO_SMALL = "amount_too_small", "Amount too small"
    AMOUNT_TOO_LARGE = "amount_too_large", "Amount too large for us"
    DEADLINE_TOO_SOON = "deadline_too_soon", "Deadline too soon"
    FUNDER = "funder", "Not a funder we work with"
    NOT_REAL = "not_real", "Not a real or open opportunity"
    DUPLICATE = "duplicate", "Duplicate of another opportunity"
    ALREADY_APPLIED = "already_applied", "Already applied or in progress"
    CAPACITY = "capacity", "No capacity right now"
    OTHER = "other", "Other"


# Statuses an opportunity can be approved or rejected from. Later pipeline stages
# (submitted, won, lost) are past the review.
REVIEWABLE_STATUSES = (
    OpportunityStatus.NEW,
    OpportunityStatus.REVIEWING,
    OpportunityStatus.PURSUING,
    OpportunityStatus.DISMISSED,
)
