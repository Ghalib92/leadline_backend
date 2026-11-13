from dataclasses import dataclass

from apps.ai.agents.source_reader import _format_links, read_source_page
from apps.ai.schemas.discovery import ExtractedCall, SourceExtraction
from apps.ai.tools.fetch.base import PageLink


@dataclass
class DummySettings:
    max_page_chars: int = 10000


@dataclass
class DummyRequest:
    opportunity_types: list[str] = ("grant", "tender")


class DummyRuntime:
    def __init__(self, extraction: SourceExtraction):
        self.request = DummyRequest()
        self.settings = DummySettings()
        self.extraction = extraction
        self.ask_calls = []

    def ask(self, role: str, schema, **kwargs):
        self.ask_calls.append({"role": role, "schema": schema, "kwargs": kwargs})
        return self.extraction


def test_format_links_various_input_types():
    page_links = [
        PageLink(url="https://example.org/1", text="Call 1"),
        PageLink(url="https://example.org/2", text=""),
    ]
    formatted = _format_links(page_links)
    assert "- Call 1: https://example.org/1" in formatted
    assert "- Link: https://example.org/2" in formatted

    tuples = [("Apply", "https://example.org/apply")]
    formatted_tuples = _format_links(tuples)
    assert "- Apply: https://example.org/apply" in formatted_tuples

    strings = ["https://example.org/raw"]
    formatted_strings = _format_links(strings)
    assert "- https://example.org/raw" in formatted_strings

    assert _format_links([]) == "(no links found on page)"


def test_read_source_page_resolves_urls_and_normalizes_types():
    raw_calls = [
        ExtractedCall(
            title="Climate Action Grant 2026",
            url="/calls/climate-2026",
            funder="Green Earth Foundation",
            summary="Funds grassroots climate projects.",
            opportunity_type="grant",
        ),
        ExtractedCall(
            title="Unknown Procurement",
            url="https://external.org/tender-123",
            funder="State Agency",
            summary="Assessment consultancy.",
            opportunity_type="nonexistent_type",
        ),
        ExtractedCall(
            title="Invalid Call With Empty URL",
            url="   ",
            funder="No URL",
            summary="Should be skipped.",
            opportunity_type="grant",
        ),
    ]
    runtime = DummyRuntime(SourceExtraction(calls=raw_calls))

    result = read_source_page(
        runtime,
        source_url="https://foundation.org/opportunities/",
        page_text="Listing of our annual funding calls.",
        links=[PageLink(url="/calls/climate-2026", text="Climate Call")],
        source_label="Foundation Funding Portal",
    )

    assert len(runtime.ask_calls) == 1
    call_args = runtime.ask_calls[0]
    assert call_args["role"] == "source_reader"
    assert call_args["kwargs"]["source_label"] == "Foundation Funding Portal"
    assert "https://foundation.org/opportunities/" in call_args["kwargs"]["source_url"]

    assert len(result.calls) == 2

    # First call: relative url resolved to absolute
    first = result.calls[0]
    assert first.title == "Climate Action Grant 2026"
    assert first.url == "https://foundation.org/calls/climate-2026"
    assert first.funder == "Green Earth Foundation"
    assert first.opportunity_type == "grant"

    # Second call: absolute URL kept, unknown type mapped to 'other'
    second = result.calls[1]
    assert second.title == "Unknown Procurement"
    assert second.url == "https://external.org/tender-123"
    assert second.opportunity_type == "other"
