"""Export a proposal version to DOCX (Word).

Section text is the simple Markdown the writer produces: paragraphs, "-" and "1."
lists, "#" sub-headings, **bold** and *italic*. Anything else is kept as plain text.
"""

import io

from docx import Document
from docx.shared import Pt

from apps.common.docx_markdown import add_markdown
from apps.organizations.choices import OpportunityType

STATUS_LABELS = {
    "ready": "Ready",
    "check": "Check",
    "missing": "Missing",
    "prepare": "Prepare",
}


def proposal_docx(proposal, version) -> bytes:
    opportunity = proposal.opportunity
    document = Document()
    document.styles["Normal"].font.size = Pt(11)
    document.add_heading(opportunity.title, level=0)
    facts = [f"Organization: {proposal.organization.name}"]
    if opportunity.funder:
        label = "Buyer" if opportunity.opportunity_type == OpportunityType.TENDER else "Funder"
        facts.append(f"{label}: {opportunity.funder}")
    reference = (opportunity.tender or {}).get("reference") or (proposal.requirements or {}).get(
        "reference"
    )
    if reference:
        facts.append(f"Tender no. {reference}")
    if opportunity.deadline:
        facts.append(f"Deadline: {opportunity.deadline:%d %B %Y}")
    facts.append(f"Version {version.number}")
    document.add_paragraph(" · ".join(facts)).italic = True

    sources = []
    for section in version.sections:
        document.add_heading(section["title"], level=1)
        add_markdown(document, section.get("content") or "[Not written yet]")
        for item in section.get("evidence") or []:
            sources.append(
                f"{section['title']} [{item['n']}]: {item['document_title']}"
                + (f" ({item['location']})" if item.get("location") else "")
            )
    if sources:
        document.add_heading("Sources (internal, remove before submitting)", level=1)
        for line in sources:
            document.add_paragraph(line, style="List Bullet")
    if proposal.checklist:
        _add_checklist(document, proposal.checklist)

    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _add_checklist(document, checklist):
    """The bid's compliance checklist, for the team (not part of the submission)."""
    document.add_heading("Compliance checklist (internal, remove before submitting)", level=1)
    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for cell, title in zip(
        table.rows[0].cells, ("Requirement", "Status", "Your documents", "In hand"), strict=True
    ):
        cell.text = title
    for item in checklist:
        cells = table.add_row().cells
        code = f"{item['code']}: " if item.get("code") else ""
        cells[0].text = f"{code}{item.get('requirement', '')}"
        note = f" ({item['note']})" if item.get("note") else ""
        cells[1].text = STATUS_LABELS.get(item.get("status"), "Check") + note
        cells[2].text = "; ".join(doc["title"] for doc in item.get("documents") or [])
        cells[3].text = "Yes" if item.get("done") else ""
