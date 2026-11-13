from django.db import models


class SuggestionKind(models.TextChoices):
    """Each kind changes one field of the organization's opportunity preferences."""

    EXCLUDE_FUNDER = "exclude_funder", "Exclude a funder"
    EXCLUDE_KEYWORD = "exclude_keyword", "Exclude a keyword"
    MIN_AMOUNT = "min_amount", "Raise the minimum amount"
    MAX_AMOUNT = "max_amount", "Lower the maximum amount"


class SuggestionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    DISMISSED = "dismissed", "Dismissed"


class EvalSuite(models.TextChoices):
    JUDGE = "judge", "Judge agreement with people"
    EXTRACTION = "extraction", "Verifier extraction accuracy"
    DRAFT = "draft", "Proposal draft quality"
