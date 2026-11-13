"""iCalendar (.ics, RFC 5545) for opportunity deadlines.

Each deadline is an all-day event on its date (in the server's time zone) with a
reminder the day before, so it shows the same in every calendar app. Written by hand:
the format is small and a dependency is not worth it.
"""

from datetime import timedelta

from django.utils import timezone

from .selectors import deadline_day, opportunity_link

PRODID = "-//Leadline//Leadline//EN"


def deadlines_ics(opportunities, *, name: str) -> bytes:
    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_text(name)}",
    ]
    for opportunity in opportunities:
        if opportunity.deadline is None:
            continue
        day = deadline_day(opportunity)
        link = opportunity_link(opportunity)
        description = "\n".join(
            part
            for part in (
                f"Funder: {opportunity.funder}" if opportunity.funder else "",
                f"Deadline: {timezone.localtime(opportunity.deadline):%d %B %Y, %H:%M %Z}",
                f"Open in Leadline: {link}",
                f"Call: {opportunity.source_url}",
            )
            if part
        )
        lines += [
            "BEGIN:VEVENT",
            f"UID:deadline-{opportunity.pk}@leadline",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_text('Deadline: ' + opportunity.title)}",
            f"DESCRIPTION:{_text(description)}",
            f"URL:{link}",  # a URI value, not escaped like TEXT
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_text('Deadline tomorrow: ' + opportunity.title)}",
            "TRIGGER:-P1D",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode()


def _text(value: str) -> str:
    """Escape a TEXT value: backslash, semicolon, comma and newlines."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space."""
    data = line.encode()
    if len(data) <= 75:
        return line
    parts, start = [], 0
    while start < len(data):
        end = min(start + (75 if not parts else 74), len(data))
        while end < len(data) and (data[end] & 0xC0) == 0x80:  # never split a character
            end -= 1
        parts.append(data[start:end].decode())
        start = end
    return "\r\n ".join(parts)
