"""Split extracted sections into overlapping passages sized for embedding and retrieval.

Splits on the largest natural boundary that fits (paragraph, line, sentence, word),
so passages rarely cut a sentence in half. Chunks never span two sections, so each
keeps its page, sheet or slide.
"""

import re
from dataclasses import dataclass

from .extraction import Section

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SEPARATORS = ("\n\n", "\n", _SENTENCE_END, " ")


@dataclass(frozen=True)
class Chunk:
    position: int
    text: str
    metadata: dict


def chunk_sections(sections: list[Section], *, size: int, overlap: int) -> list[Chunk]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("Chunk size must be positive and overlap smaller than the size.")
    chunks = []
    for section in sections:
        for text in split_text(section.text, size=size, overlap=overlap):
            chunks.append(Chunk(position=len(chunks), text=text, metadata=dict(section.metadata)))
    return chunks


def split_text(text: str, *, size: int, overlap: int) -> list[str]:
    pieces = _pieces(text.strip(), size, _SEPARATORS)
    chunks, current = [], ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip() if current else piece
        if len(candidate) <= size:
            current = candidate
            continue
        chunks.append(current)
        current = f"{_tail(current, overlap)} {piece}".strip()
        if len(current) > size:  # the overlap cannot make a chunk exceed the limit
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _pieces(text: str, size: int, separators) -> list[str]:
    """Break text into pieces no longer than `size`, preferring the first separator."""
    if len(text) <= size:
        return [text] if text else []
    if not separators:
        return [text[start : start + size] for start in range(0, len(text), size)]
    separator, rest = separators[0], separators[1:]
    parts = separator.split(text) if isinstance(separator, re.Pattern) else text.split(separator)
    if len(parts) == 1:
        return _pieces(text, size, rest)
    pieces = []
    for part in parts:
        pieces.extend(_pieces(part.strip(), size, rest))
    return pieces


def _tail(text: str, length: int) -> str:
    """Up to the last `length` characters, starting at a word boundary (or nothing)."""
    if length <= 0 or not text:
        return ""
    if len(text) <= length:
        return text
    tail = text[-length:]
    return tail.split(" ", 1)[1] if " " in tail else ""
