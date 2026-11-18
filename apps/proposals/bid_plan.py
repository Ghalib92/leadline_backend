"""A bid's deadline plan: the steps to a submitted tender, counted back from closing.

Plain code, worked out when the proposal is read, so it stays right as days pass.
Dates the tender itself sets (clarification deadline, pre-bid meeting, site visit) are
used as given; the other steps are due a set number of days before closing
(proposals.yaml `bid_plan`), moved off weekends to the Friday before. With too little
time left, a step that should already be done is due today and the plan is "tight".
"""

from datetime import date, datetime, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

TENDER_DATE_KINDS = ("clarification", "meeting", "site_visit")


def _weekday(day: date) -> date:
    """Saturday and Sunday move back to Friday."""
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def closing_time(opportunity) -> datetime | None:
    """The portal's closing time when known (the deadline is often a date only, which
    would read as midnight), else the deadline."""
    raw = (opportunity.tender or {}).get("closes_at") or ""
    parsed = parse_datetime(raw) if raw else None
    if parsed is not None:
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return timezone.localtime(parsed)
    if opportunity.deadline:
        return timezone.localtime(opportunity.deadline)
    return None


def bid_plan(opportunity, requirements: dict, checklist: list, settings, today=None) -> dict | None:
    """{closes_at, days_left, closed, tight, steps: [{key, title, due, time, state,
    from_tender, note}]}; None when the closing date is unknown."""
    closes = closing_time(opportunity)
    if closes is None:
        return None
    today = today or timezone.localdate()
    closing_day = closes.date()
    days_left = (closing_day - today).days
    requirements = requirements or {}
    tight = False
    steps = []

    def due_before(days: int) -> date:
        nonlocal tight
        due = _weekday(closing_day - timedelta(days=days))
        if due < today <= closing_day:
            tight = True
            return today
        return due

    def add(key, title, due, *, note="", time="", from_tender=False, done=False):
        steps.append(
            {
                "key": key,
                "title": title,
                "due": due.isoformat(),
                "time": time,
                "from_tender": from_tender,
                "note": note,
                "state": _state(due, today, done),
            }
        )

    tender_dates = _tender_dates(requirements, closing_day)
    if not any(d["kind"] == "clarification" for d in tender_dates):
        add(
            "clarifications",
            "Send any questions to the buyer",
            due_before(settings.clarifications),
            note="Ask in writing; the answers usually go to every bidder.",
        )
    for index, item in enumerate(tender_dates):
        add(
            f"tender_{index + 1}",
            item["event"],
            item["date"],
            time=item["time"],
            from_tender=True,
        )

    security = requirements.get("bid_security") or (opportunity.tender or {}).get("bid_security")
    security_items = [item for item in checklist if item.get("kind") == "security"]
    if security or security_items:
        add(
            "bid_security",
            "Get the bid security from your bank or insurer",
            due_before(settings.bid_security),
            note=f"{security}." if security else "",
            done=bool(security_items) and all(item.get("done") for item in security_items),
        )

    missing = [
        item for item in checklist if item.get("status") == "missing" and not item.get("done")
    ]
    if checklist:
        add(
            "documents",
            "Collect the missing documents",
            due_before(settings.documents),
            note=_missing_note(missing),
            done=not missing,
        )
    add("draft", "Finish the technical proposal", due_before(settings.draft))
    add("review", "Read it through, sign and stamp every page", due_before(settings.review))
    submission = requirements.get("submission") or (opportunity.tender or {}).get("submission")
    add(
        "submit",
        "Submit the bid",
        due_before(settings.submit),
        note=("; ".join(submission) if isinstance(submission, list) else submission or "")[:300],
    )
    add("closing", "Tender closes", closing_day, time=closes.strftime("%H:%M"), from_tender=True)

    order = {step["key"]: index for index, step in enumerate(steps)}
    steps.sort(key=lambda step: (step["due"], step["key"] == "closing", order[step["key"]]))
    return {
        "closes_at": closes.isoformat(),
        "days_left": days_left,
        "closed": closes <= timezone.now(),
        "tight": tight,
        "steps": steps,
    }


def _short(text: str, limit: int = 70) -> str:
    text = " ".join(str(text or "").split()).rstrip(".;: ")
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _missing_note(missing: list[dict]) -> str:
    if not missing:
        return "Nothing marked missing."
    names = "; ".join(_short(item.get("requirement", "")) for item in missing[:3])
    more = f"; and {len(missing) - 3} more" if len(missing) > 3 else ""
    return f"{len(missing)} missing: {names}{more}."


def _tender_dates(requirements: dict, closing_day: date) -> list[dict]:
    """The tender's own dates before closing (questions, meetings, visits)."""
    found = []
    for item in requirements.get("key_dates") or []:
        if not isinstance(item, dict) or item.get("kind") not in TENDER_DATE_KINDS:
            continue
        day = parse_date(str(item.get("date") or "")[:10])
        if day is None or day > closing_day:
            continue
        found.append(
            {
                "event": str(item.get("event") or "Tender date")[:150],
                "date": day,
                "time": str(item.get("time") or "")[:5],
                "kind": item["kind"],
            }
        )
    return found


def _state(due: date, today: date, done: bool) -> str:
    if done:
        return "done"
    if due < today:
        return "past"
    if due == today:
        return "today"
    return "upcoming"
