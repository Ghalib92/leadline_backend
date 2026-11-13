from django.db import models


class ProposalStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    DRAFTING = "drafting", "Drafting"  # a full draft or a section rewrite is running
    READY = "ready", "Ready for your edits"
    FAILED = "failed", "Failed"  # the last job failed; earlier versions are kept


BUSY_STATUSES = (ProposalStatus.QUEUED, ProposalStatus.DRAFTING)


class ProposalJob(models.TextChoices):
    DRAFT = "draft", "Full draft"
    SECTION = "section", "Rewrite one section"


class ProposalStep(models.TextChoices):
    REQUIREMENTS = "requirements", "Read the call's requirements"
    OUTLINE = "outline", "Plan the sections"
    EVIDENCE = "evidence", "Find evidence in company documents"
    DRAFT = "draft", "Write the sections"
    REVIEW = "review", "Check the draft"


class VersionSource(models.TextChoices):
    AI_DRAFT = "ai_draft", "AI draft"
    AI_SECTION = "ai_section", "AI rewrote a section"
    EDIT = "edit", "Edited"
    RESTORE = "restore", "Restored an earlier version"
