"""Download a public document (PDF or Word) and read its text, page by page.

Same rules as page fetches: robots.txt is honoured, only public addresses are opened
(tools.http), and the download is capped. The text is read with the knowledge app's
extractors, the ones used for uploaded documents.
"""

import io
import os
from urllib.parse import unquote, urlsplit

import httpx

from apps.ai.tools.fetch.http import Robots
from apps.ai.tools.http import UnsafeURL, get_http_client, safe_get
from apps.knowledge.extraction import ExtractionError, UnsupportedDocument, extract_sections

from .base import DocumentError, DocumentPage, DocumentText

TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
EXTENSIONS = frozenset(TYPES.values())
# Text kept per document; the page picker chooses what reaches a model.
MAX_TEXT_CHARS = 600_000


class HttpDocumentReader:
    def __init__(self, *, max_bytes: int, timeout: float = 60, robots: Robots | None = None):
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.robots = robots or Robots()

    def read(self, url: str, *, name: str = "") -> DocumentText:
        if not self.robots.allowed(url):
            raise DocumentError("The site's robots.txt does not allow automated reading.")
        body, final_url, extension = self._download(url)
        filename = f"document{extension}"
        try:
            sections = extract_sections(io.BytesIO(body), filename, max_chars=MAX_TEXT_CHARS)
        except UnsupportedDocument as exc:
            raise DocumentError(str(exc)) from exc
        except ExtractionError as exc:
            raise DocumentError("The document could not be read (damaged or unusual).") from exc
        pages = tuple(
            DocumentPage(number=section.metadata.get("page", index), text=section.text)
            for index, section in enumerate(sections, start=1)
        )
        return DocumentText(url=final_url, name=name or _name_from(final_url), pages=pages)

    def _download(self, url: str) -> tuple[bytes, str, str]:
        with get_http_client(timeout=self.timeout) as client:
            try:
                response = safe_get(client, url, stream=True)
            except UnsafeURL as exc:
                raise DocumentError(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise DocumentError(
                    f"The document could not be downloaded ({type(exc).__name__})."
                ) from exc
            try:
                if response.status_code >= 400:
                    raise DocumentError(f"The document returned HTTP {response.status_code}.")
                final_url = str(response.url)
                extension = _extension(response.headers.get("content-type", ""), final_url)
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_bytes:
                    raise DocumentError(_too_big(self.max_bytes))
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > self.max_bytes:
                        raise DocumentError(_too_big(self.max_bytes))
            except httpx.HTTPError as exc:
                raise DocumentError(
                    f"The document download broke off ({type(exc).__name__})."
                ) from exc
            finally:
                response.close()
        return body, final_url, extension


def _extension(content_type: str, url: str) -> str:
    """.pdf or .docx, from the content type, else the URL (servers often send
    application/octet-stream)."""
    kind = content_type.split(";")[0].strip().lower()
    if kind in TYPES:
        return TYPES[kind]
    extension = os.path.splitext(urlsplit(url).path)[1].lower()
    if extension in EXTENSIONS and kind in ("", "application/octet-stream", "binary/octet-stream"):
        return extension
    raise DocumentError(f"Not a PDF or Word document ({kind or 'unknown type'}).")


def _name_from(url: str) -> str:
    return unquote(os.path.basename(urlsplit(url).path)) or "Document"


def _too_big(limit: int) -> str:
    return f"The document is larger than {limit // 1_000_000} MB, so it was not read."


def is_document_url(url: str) -> bool:
    """A link that ends in .pdf or .docx (a web page's link to a tender document)."""
    return os.path.splitext(urlsplit(url).path)[1].lower() in EXTENSIONS
