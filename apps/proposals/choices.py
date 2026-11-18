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
    CHECKLIST = "checklist", "Check documents against a tender's requirements"


class ProposalStep(models.TextChoices):
    REQUIREMENTS = "requirements", "Read the call's requirements"
    CHECKLIST = "checklist", "Check company documents against the requirements"
    OUTLINE = "outline", "Plan the sections"
    EVIDENCE = "evidence", "Find evidence in company documents"
    DRAFT = "draft", "Write the sections"
    REVIEW = "review", "Check the draft"


class VersionSource(models.TextChoices):
    AI_DRAFT = "ai_draft", "AI draft"
    AI_SECTION = "ai_section", "AI rewrote a section"
    EDIT = "edit", "Edited"
    RESTORE = "restore", "Restored an earlier version"


class ChecklistStatus(models.TextChoices):
    """A tender requirement against the organization's documents (the AI's verdict)."""

    READY = "ready", "Covered by a document"
    CHECK = "check", "Check the document"
    MISSING = "missing", "Missing"
    PREPARE = "prepare", "Prepare for this bid"
