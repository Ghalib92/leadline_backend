"""Turn an uploaded file into plain-text sections, one per page, sheet or slide.

Each section keeps where it came from, so chunks (and later the Proposal Writer's
citations) can point back to "page 3" or "slide 7".
"""

import csv
import io
import os
import re
from dataclasses import dataclass, field

# Postgres text cannot hold NUL, and the other C0 controls are noise from PDF fonts.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# Spreadsheets can hold millions of cells; past this a sheet is mostly numbers anyway.
MAX_SHEET_ROWS = 2_000


class ExtractionError(Exception):
    """The file should be readable but is not (corrupt, truncated). Retrying may help."""


class UnsupportedDocument(Exception):
    """This kind of file cannot be read yet. The message is shown to the user."""


@dataclass(frozen=True)
class Section:
    text: str
    metadata: dict = field(default_factory=dict)


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub("", text)
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def extract_sections(fileobj, filename: str, *, max_chars: int) -> list[Section]:
    """Read `fileobj` (binary) according to the extension of `filename`.

    Text past `max_chars` is dropped. Raises UnsupportedDocument or ExtractionError.
    """
    extension = os.path.splitext(filename)[1].lower()
    reader = _READERS.get(extension)
    if reader is None:
        raise UnsupportedDocument(
            f"{extension or 'This'} files can't be read yet. Upload a PDF, Word, Excel, "
            "PowerPoint or text version instead."
        )
    try:
        raw_sections = list(reader(fileobj))  # readers are generators: errors surface here
    except UnsupportedDocument:
        raise
    except Exception as exc:
        raise ExtractionError(f"The {extension} file could not be read.") from exc

    sections, remaining = [], max_chars
    for section in raw_sections:
        text = clean_text(section.text)
        if not text:
            continue
        if len(text) > remaining:
            text = text[:remaining]
        sections.append(Section(text=text, metadata=section.metadata))
        remaining -= len(text)
        if remaining <= 0:
            break
    if not sections:
        if extension == ".pdf":
            raise UnsupportedDocument(
                "No text found in this PDF. It may be a scan; upload a text-based PDF or a "
                "Word version instead."
            )
        raise UnsupportedDocument("No text found in this file.")
    return sections


def _read_pdf(fileobj):
    from pypdf import PdfReader

    reader = PdfReader(fileobj)
    if reader.is_encrypted and not reader.decrypt(""):
        raise UnsupportedDocument("This PDF is password-protected. Upload an unlocked copy.")
    for number, page in enumerate(reader.pages, start=1):
        yield Section(page.extract_text() or "", {"page": number})


def _read_docx(fileobj):
    from docx import Document

    document = Document(fileobj)
    lines = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = _unique_cells(cell.text.strip() for cell in row.cells)
            if cells:
                lines.append(" | ".join(cells))
    yield Section("\n".join(lines))


def _unique_cells(cells):
    # Merged Word table cells repeat their text once per spanned column.
    unique = []
    for cell in cells:
        if cell and (not unique or unique[-1] != cell):
            unique.append(cell)
    return unique


def _read_xlsx(fileobj):
    from openpyxl import load_workbook

    workbook = load_workbook(fileobj, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            lines = []
            for row in sheet.iter_rows(max_row=MAX_SHEET_ROWS, values_only=True):
                values = [str(value).strip() for value in row if value not in (None, "")]
                if values:
                    lines.append(" | ".join(values))
            yield Section("\n".join(lines), {"sheet": sheet.title})
    finally:
        workbook.close()


def _read_pptx(fileobj):
    from pptx import Presentation

    presentation = Presentation(fileobj)
    for number, slide in enumerate(presentation.slides, start=1):
        lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                lines.append(shape.text_frame.text)
            elif getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    lines.append(" | ".join(cell.text for cell in row.cells if cell.text))
        if slide.has_notes_slide:
            lines.append(slide.notes_slide.notes_text_frame.text)
        yield Section("\n".join(lines), {"slide": number})


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Legacy Windows exports; cp1252 maps almost every byte, the rest are replaced.
        return data.decode("cp1252", errors="replace")


def _read_text(fileobj):
    yield Section(_decode(fileobj.read()))


def _read_csv(fileobj):
    text = _decode(fileobj.read())
    rows = csv.reader(io.StringIO(text))
    lines = []
    for index, row in enumerate(rows):
        if index >= MAX_SHEET_ROWS:
            break
        values = [value.strip() for value in row if value.strip()]
        if values:
            lines.append(" | ".join(values))
    yield Section("\n".join(lines))


_READERS = {
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".xlsx": _read_xlsx,
    ".pptx": _read_pptx,
    ".txt": _read_text,
    ".md": _read_text,
    ".csv": _read_csv,
}

SUPPORTED_EXTENSIONS = frozenset(_READERS)
