import io

import pytest

from apps.knowledge.chunking import chunk_sections, split_text
from apps.knowledge.extraction import (
    ExtractionError,
    Section,
    UnsupportedDocument,
    clean_text,
    extract_sections,
)

from .files import make_docx, make_pdf, make_pptx, make_xlsx


def extract(data, filename, max_chars=100_000):
    return extract_sections(io.BytesIO(data), filename, max_chars=max_chars)


def test_pdf_pages_keep_their_numbers():
    data = make_pdf(["Clean water for Turkana (2019)", "", "Audited accounts"])
    sections = extract(data, "a.pdf")
    assert [(s.text, s.metadata) for s in sections] == [
        ("Clean water for Turkana (2019)", {"page": 1}),
        ("Audited accounts", {"page": 3}),
    ]


def test_docx_paragraphs_and_tables():
    data = make_docx(
        ["Acme Africa", "We train 2,000 youth a year."],
        table=[["Year", "Funder"], ["2024", "Mastercard Foundation"]],
    )
    [section] = extract(data, "Profile.DOCX")
    assert "We train 2,000 youth a year." in section.text
    assert "2024 | Mastercard Foundation" in section.text


def test_xlsx_one_section_per_sheet():
    data = make_xlsx({"Budget": [["Item", "USD"], ["Staff", 12000]], "Empty": []})
    [section] = extract(data, "budget.xlsx")
    assert section.metadata == {"sheet": "Budget"}
    assert section.text == "Item | USD\nStaff | 12000"


def test_pptx_slides_and_notes():
    data = make_pptx([("Our impact", "40 schools reached")], notes="Mention the 2023 pilot")
    [section] = extract(data, "deck.pptx")
    assert section.metadata == {"slide": 1}
    assert "40 schools reached" in section.text and "2023 pilot" in section.text


@pytest.mark.parametrize("name", ["notes.txt", "notes.md"])
def test_text_files(name):
    [section] = extract("Ni sawa — café".encode(), name)
    assert section.text == "Ni sawa — café"


def test_text_falls_back_to_cp1252_and_csv_is_tabular():
    [section] = extract("Caf\xe9".encode("cp1252"), "legacy.txt")
    assert section.text == "Café"
    [section] = extract(b"name,amount\nUNICEF,5000\n,,\n", "funders.csv")
    assert section.text == "name | amount\nUNICEF | 5000"


@pytest.mark.parametrize("name", ["scan.png", "old.doc", "sheet.ods", "noextension"])
def test_unsupported_types(name):
    with pytest.raises(UnsupportedDocument, match="can't be read yet"):
        extract(b"data", name)


def test_pdf_without_text_is_unsupported():
    with pytest.raises(UnsupportedDocument, match="may be a scan"):
        extract(make_pdf([""]), "scan.pdf")


def test_corrupt_files_are_extraction_errors():
    with pytest.raises(ExtractionError):
        extract(b"%PDF-1.4 example", "broken.pdf")
    with pytest.raises(ExtractionError):
        extract(b"not a zip", "broken.docx")


def test_text_is_cleaned_and_capped():
    assert clean_text("a\x00b\r\n\n\n\n  c \t d e  ") == "ab\n\nc d e"
    sections = extract(make_pdf(["x" * 30, "y" * 30, "z" * 30]), "a.pdf", max_chars=45)
    assert [len(s.text) for s in sections] == [30, 15]


def test_split_text_respects_size_and_overlaps():
    text = " ".join(f"Sentence number {n} is here." for n in range(40))
    chunks = split_text(text, size=120, overlap=30)
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert len(chunks) > 5
    # Consecutive chunks share some words.
    assert any(word in chunks[1] for word in chunks[0].split()[-3:])
    assert split_text("short", size=120, overlap=30) == ["short"]
    assert split_text("", size=120, overlap=30) == []


def test_split_text_cuts_words_longer_than_a_chunk():
    chunks = split_text("a" * 250, size=100, overlap=10)
    assert chunks == ["a" * 100, "a" * 100, "a" * 50]


def test_chunks_are_numbered_and_keep_section_metadata():
    sections = [Section("one two three", {"page": 1}), Section("four", {"page": 2})]
    chunks = chunk_sections(sections, size=8, overlap=0)
    assert [(c.position, c.text, c.metadata) for c in chunks] == [
        (0, "one two", {"page": 1}),
        (1, "three", {"page": 1}),
        (2, "four", {"page": 2}),
    ]
    with pytest.raises(ValueError):
        chunk_sections(sections, size=10, overlap=10)
