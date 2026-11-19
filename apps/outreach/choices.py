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


# ---------------------------------------------------------------------------
# Client leads: pitches and the call list
# ---------------------------------------------------------------------------


class PitchStatus(models.TextChoices):
    """A pitch's, or a job application's, writing state."""

    WRITING = "writing", "AI is writing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class LeadChannel(models.TextChoices):
    """How a person reached (or tried to reach) a business, or applied for a job."""

    CALL = "call", "Phone call"
    WHATSAPP = "whatsapp", "WhatsApp"
    EMAIL = "email", "Email"
    VISIT = "visit", "Visit"
    ONLINE = "online", "Online form or job site"
    OTHER = "other", "Other"


class LeadOutcome(models.TextChoices):
    """What came of it. Kept about the conversation, so the call list can sort by it."""

    NO_ANSWER = "no_answer", "No answer"
    CALL_BACK = "call_back", "Asked us to call back"
    MESSAGE_SENT = "message_sent", "Message sent, waiting"
    TALKED = "talked", "Talked, not decided"
    INTERESTED = "interested", "Interested"
    MEETING = "meeting", "Meeting booked"
    NOT_INTERESTED = "not_interested", "Not interested"
    WRONG_CONTACT = "wrong_contact", "Wrong number or contact"
    # A job seeker's applications (the same log, the tracker's stages).
    APPLIED = "applied", "Applied"
    INTERVIEW = "interview", "Interview booked"
    OFFER = "offer", "Offer received"
    REJECTED = "rejected", "Not selected"
    WITHDREW = "withdrew", "Withdrew"


JOB_OUTCOMES = (
    LeadOutcome.APPLIED,
    LeadOutcome.INTERVIEW,
    LeadOutcome.OFFER,
    LeadOutcome.REJECTED,
    LeadOutcome.WITHDREW,
)
CLIENT_OUTCOMES = tuple(outcome for outcome in LeadOutcome if outcome not in JOB_OUTCOMES)

# Outcomes after which nobody needs to reach the business (or employer) again.
CLOSED_OUTCOMES = (
    LeadOutcome.NOT_INTERESTED,
    LeadOutcome.WRONG_CONTACT,
    LeadOutcome.REJECTED,
    LeadOutcome.WITHDREW,
)


class CallListView(models.TextChoices):
    TO_CONTACT = "to_contact", "To contact"  # approved, never reached out to (or applied)
    FOLLOW_UP = "follow_up", "Follow up"  # reached out, not closed
    CLOSED = "closed", "Closed"  # not interested or wrong contact
    ALL = "all", "All"
