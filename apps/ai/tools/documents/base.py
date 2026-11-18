"""Reading documents (tender PDFs, terms of reference) behind one interface."""

from dataclasses import dataclass
from typing import Protocol


class DocumentError(Exception):
    """The document could not be read. The message is safe to show in the timeline."""


@dataclass(frozen=True)
class DocumentPage:
    number: int  # 1-based; Word documents are one "page"
    text: str


@dataclass(frozen=True)
class DocumentText:
    url: str  # after redirects
    name: str
    pages: tuple[DocumentPage, ...]

    @property
    def chars(self) -> int:
        return sum(len(page.text) for page in self.pages)


class DocumentReader(Protocol):
    def read(self, url: str, *, name: str = "") -> DocumentText: ...
