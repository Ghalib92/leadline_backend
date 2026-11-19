"""Simple Markdown into a Word document: paragraphs, "-" and "1." lists, "#" headings,
**bold** and *italic*. Anything else is kept as plain text. Used by the proposal and
job-application exports."""

import re

_INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")
_NUMBERED = re.compile(r"^\d+[.)]\s+")


def add_markdown(document, text, *, top_level=2):
    """`top_level` is the Word heading level of "#" (0 is the document title)."""
    pending: list[str] = []  # lines of the current paragraph

    def flush():
        if pending:
            add_runs(document.add_paragraph(), " ".join(pending))
            pending.clear()

    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("#"):
            flush()
            hashes = len(stripped) - len(stripped.lstrip("#"))
            level = min(4, top_level + hashes - 1)
            document.add_heading(stripped.lstrip("#").strip(), level=level)
        elif stripped[:2] in ("- ", "* ", "• "):
            flush()
            add_runs(document.add_paragraph(style="List Bullet"), stripped[2:])
        elif _NUMBERED.match(stripped):
            flush()
            add_runs(document.add_paragraph(style="List Number"), _NUMBERED.sub("", stripped))
        else:
            pending.append(stripped)
    flush()


def add_runs(paragraph, text):
    for part in _INLINE.split(text):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            paragraph.add_run(part[1:-1]).italic = True
        elif part:
            paragraph.add_run(part)
