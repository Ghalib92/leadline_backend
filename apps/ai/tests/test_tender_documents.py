"""Tender documents: downloading and reading them, and picking the pages that matter."""

import io

import httpx
import pytest
from docx import Document

from apps.ai.testing import make_pdf
from apps.ai.tools.documents import (
    DocumentError,
    DocumentPage,
    HttpDocumentReader,
    is_document_url,
    pick_pages,
)
from apps.ai.tools.documents.pages import heading_priority, is_contents_page

PDF = "application/pdf"
BOILERPLATE = "The Tenderer shall bear all costs associated with its Tender. " * 20


def pages(*texts):
    return [DocumentPage(number, text) for number, text in enumerate(texts, start=1)]


# -- picking pages ----------------------------------------------------------------------


def test_short_documents_are_read_whole():
    picked = pick_pages([("Notice", pages("Tender notice", "", "Closing 19 Nov"))], max_chars=1000)
    assert [(p.document, p.number) for p in picked] == [("Notice", 1), ("Notice", 3)]


def test_long_documents_are_read_by_heading_then_words():
    document = pages(
        "TABLE OF CONTENTS\nData Sheet ........ 21\nEvaluation ...... 25\nTerms ..... 54\n"
        "Forms ...... 60\nAnnex ...... 70",
        "SECTION 2 (A). INSTRUCTIONS TO TENDERERS\n" + BOILERPLATE,
        # "Data Sheet" in running text is not a heading.
        "16.4 The price shall be stated in the currency stated in the\n"
        "Data Sheet. If indicated in the Data Sheet, local costs are in Kenya Shillings.\n"
        + BOILERPLATE,
        "21\nSECTION 2 (B). DATA SHEET\n22.1 Othereligibilityandmandatorycriteria: MR1",
        "22\nReference to ITC Clause\nMR12 Self-declaration form",
        "23\n18.5 The proposals must be submitted no later than 19 November",
        BOILERPLATE,
        "54\nSECTION 5. TERMS OF REFERENCE\nDevelop a county dashboard",
        BOILERPLATE,
        "Certificate of incorporation, tax compliance certificate and AGPO are mandatory.",
    )
    budget = sum(len(page.text) for page in document) // 2
    picked = pick_pages([("Tender document", document)], max_chars=budget)
    numbers = [page.number for page in picked]
    # The Data Sheet and the two pages after it, the terms of reference, and a page
    # full of requirement words; never the contents page, in document order.
    assert numbers[:3] == [4, 5, 6]
    assert 8 in numbers and 10 in numbers
    assert 1 not in numbers and 3 not in numbers and 2 not in numbers
    assert numbers == sorted(numbers)
    assert sum(len(page.text) for page in picked) <= budget


def test_headings_count_only_at_the_top_of_a_page():
    assert heading_priority("SECTION 2 (B). DATA SHEET\n...") == 0
    assert heading_priority("PARTICULARS OF APPENDIX TO INSTRUCTIONS TO TENDERS") == 0
    assert heading_priority("22.2 TheCriteria,sub-criteria,andpointsystem") == 1
    assert heading_priority("SECTION V - TERMS OF REFERENCE") == 2
    assert heading_priority("1\n2\n3\n4\n5\n6\nTERMS OF REFERENCE") is None
    assert (
        heading_priority("Data Sheet. If indicated in the Data Sheet, the price is local.") is None
    )
    assert is_contents_page("A .... 1\nB .... 2\nC .... 3\nD .... 4\nE .... 5")


def test_documents_share_the_budget_in_order():
    first = pages("SECTION 2 (B). DATA SHEET\n" + "x" * 500, BOILERPLATE)
    second = pages("TERMS OF REFERENCE\n" + "y" * 500, BOILERPLATE)
    picked = pick_pages([("Tender", first), ("ToR", second)], max_chars=1200)
    assert [(p.document, p.number) for p in picked] == [("Tender", 1), ("ToR", 1)]


# -- downloading ------------------------------------------------------------------------


@pytest.fixture
def portal(web):
    web.route("tenders.go.ke", "/robots.txt", lambda r: httpx.Response(200, text="User-agent: *"))
    return web


def reader(**kwargs):
    return HttpDocumentReader(max_bytes=kwargs.pop("max_bytes", 1_000_000), **kwargs)


def test_reads_a_pdf_page_by_page(portal):
    body = make_pdf(["SECTION 2 (B). DATA SHEET\nMR1 Certificate of incorporation", "Page 2"])
    portal.route(
        "tenders.go.ke",
        "/storage/t.pdf",
        lambda r: httpx.Response(200, content=body, headers={"content-type": PDF}),
    )
    document = reader().read("https://tenders.go.ke/storage/t.pdf", name="Tender document")
    assert document.name == "Tender document"
    assert [page.number for page in document.pages] == [1, 2]
    assert "MR1 Certificate of incorporation" in document.pages[0].text
    assert document.chars > 0


def test_reads_word_documents_and_octet_streams_named_pdf(portal):
    word = Document()
    word.add_paragraph("Terms of reference: build a portal")
    out = io.BytesIO()
    word.save(out)
    portal.route(
        "tenders.go.ke", "/tor.docx", lambda r: httpx.Response(200, content=out.getvalue())
    )
    portal.route(
        "tenders.go.ke",
        "/notice.pdf",
        lambda r: httpx.Response(
            200, content=make_pdf(["Notice"]), headers={"content-type": "application/octet-stream"}
        ),
    )
    tor = reader().read("https://tenders.go.ke/tor.docx")
    assert tor.name == "tor.docx"
    assert "build a portal" in tor.pages[0].text
    assert reader().read("https://tenders.go.ke/notice.pdf").pages[0].text == "Notice"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "HTTP 404"),
        (
            httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"}),
            "Not a PDF",
        ),
        (
            httpx.Response(200, content=b"%PDF-1.4 broken", headers={"content-type": PDF}),
            "could not",
        ),
        (
            # Refused from the declared size, before reading the body.
            httpx.Response(
                200, content=b"x" * 20, headers={"content-type": PDF, "content-length": "2000"}
            ),
            "larger than",
        ),
    ],
)
def test_unreadable_documents_raise(portal, response, message):
    portal.route("tenders.go.ke", "/d.pdf", lambda r: response)
    with pytest.raises(DocumentError, match=message):
        reader(max_bytes=1000).read("https://tenders.go.ke/d.pdf")


def test_a_download_is_cut_off_past_the_limit(portal):
    def stream(request):
        return httpx.Response(200, headers={"content-type": PDF}, content=iter([b"x" * 600] * 3))

    portal.route("tenders.go.ke", "/big.pdf", stream)
    with pytest.raises(DocumentError, match="larger than"):
        reader(max_bytes=1000).read("https://tenders.go.ke/big.pdf")


def test_robots_txt_is_honoured(web):
    web.route(
        "county.go.ke",
        "/robots.txt",
        lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /"),
    )
    with pytest.raises(DocumentError, match="robots.txt"):
        reader().read("https://county.go.ke/tender.pdf")


def test_document_links():
    assert is_document_url("https://kwale.go.ke/files/Tender%20Doc.PDF")
    assert is_document_url("https://x.go.ke/tor.docx")
    assert not is_document_url("https://x.go.ke/tenders/12")
