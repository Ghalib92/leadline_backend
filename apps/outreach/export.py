"""Export a job application to DOCX (Word): the tailored CV, then the cover letter on a
new page. The notes on gaps are for the candidate and stay out of the file."""

import io

from docx import Document
from docx.enum.text import WD_BREAK
from docx.shared import Pt

from apps.common.docx_markdown import add_markdown


def application_docx(application) -> bytes:
    document = Document()
    document.styles["Normal"].font.size = Pt(11)
    if application.cv.strip():
        add_markdown(document, application.cv, top_level=0)  # "# Name" is the title
    if application.cover_letter.strip():
        if application.cv.strip():
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        document.add_heading("Cover letter", level=1)
        for block in application.cover_letter.strip().split("\n\n"):
            paragraph = document.add_paragraph()
            for number, line in enumerate(line.strip() for line in block.splitlines()):
                if number:
                    paragraph.add_run().add_break()  # a sign-off keeps its lines
                paragraph.add_run(line)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()
