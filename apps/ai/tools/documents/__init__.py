"""Tender documents: download (PDF, Word), read page by page, and pick the pages that
hold the requirements (`pages.pick_pages`)."""

from .base import DocumentError, DocumentPage, DocumentReader, DocumentText
from .http import HttpDocumentReader, is_document_url
from .pages import PickedPage, pick_pages

__all__ = [
    "DocumentError",
    "DocumentPage",
    "DocumentReader",
    "DocumentText",
    "HttpDocumentReader",
    "PickedPage",
    "is_document_url",
    "pick_pages",
]
