from django.db import models


class ContactSource(models.TextChoices):
    PAGE = "page", "Found on a web page"
    HUNTER = "hunter", "Hunter.io"
    MANUAL = "manual", "Added by a person"


class ContactKind(models.TextChoices):
    PERSON = "person", "Person"
    INBOX = "inbox", "Team inbox"


class SearchStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class EmailPurpose(models.TextChoices):
    INTRODUCTION = "introduction", "Introduce us and our interest"
    QUESTION = "question", "Ask a question about the call"
    MEETING = "meeting", "Request a short call or meeting"
    FOLLOW_UP = "follow_up", "Follow up after applying"
    OTHER = "other", "Other (see instructions)"


class EmailStatus(models.TextChoices):
    DRAFTING = "drafting", "AI is drafting"
    DRAFT = "draft", "Draft, awaiting approval"
    SENDING = "sending", "Approved, sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"  # drafting or sending failed
    DISCARDED = "discarded", "Discarded"


EDITABLE_EMAIL_STATUSES = (EmailStatus.DRAFT, EmailStatus.FAILED)
