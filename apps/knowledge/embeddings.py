"""Text embeddings behind a small interface, so the provider can be swapped.

KNOWLEDGE_EMBEDDING_PROVIDER picks the provider; the model and its dimensions come
from settings too. "fastembed" runs locally on CPU (no API key). "hashing" is a
deterministic bag-of-words embedder with no dependencies, for tests and offline
development; its search quality is only good enough to exercise the pipeline.
"""

import hashlib
import math
import re
from collections.abc import Callable, Sequence
from functools import cache
from typing import Protocol

from django.conf import settings

from apps.ai.exceptions import AIConfigError


class Embedder(Protocol):
    dimensions: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


EmbedderFactory = Callable[[str, int], Embedder]

_PROVIDERS: dict[str, EmbedderFactory] = {}


def register_embedder(name: str, factory: EmbedderFactory) -> None:
    _PROVIDERS[name] = factory


def registered_embedders() -> frozenset[str]:
    return frozenset(_PROVIDERS)


def get_embedder() -> Embedder:
    return _build(
        settings.KNOWLEDGE_EMBEDDING_PROVIDER,
        settings.KNOWLEDGE_EMBEDDING_MODEL,
        settings.KNOWLEDGE_EMBEDDING_DIMENSIONS,
    )


@cache
def _build(provider: str, model: str, dimensions: int) -> Embedder:
    # Cached: loading a local model takes seconds, and workers embed many documents.
    factory = _PROVIDERS.get(provider)
    if factory is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise AIConfigError(
            f"Unknown KNOWLEDGE_EMBEDDING_PROVIDER {provider!r}. Known providers: {known}."
        )
    return factory(model, dimensions)


class FastEmbedEmbedder:
    batch_size = 32

    def __init__(self, model: str, dimensions: int):
        from fastembed import TextEmbedding

        supported = {item["model"]: item["dim"] for item in TextEmbedding.list_supported_models()}
        if model not in supported:
            raise AIConfigError(f"fastembed does not support KNOWLEDGE_EMBEDDING_MODEL {model!r}.")
        if supported[model] != dimensions:
            raise AIConfigError(
                f"{model} produces {supported[model]}-dimensional vectors but "
                f"KNOWLEDGE_EMBEDDING_DIMENSIONS is {dimensions}."
            )
        self.dimensions = dimensions
        self._model = TextEmbedding(
            model_name=model, cache_dir=settings.KNOWLEDGE_EMBEDDING_CACHE_DIR or None
        )

    def embed_documents(self, texts):
        vectors = self._model.passage_embed(list(texts), batch_size=self.batch_size)
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text):
        # BGE-style models embed queries with an instruction prefix; fastembed adds it.
        return next(iter(self._model.query_embed(text))).tolist()


_TOKEN = re.compile(r"\w+")


class HashingEmbedder:
    """Feature hashing over lower-cased words: identical words give similar vectors."""

    def __init__(self, model: str, dimensions: int):
        self.dimensions = dimensions

    def embed_documents(self, texts):
        return [self._embed(text) for text in texts]

    def embed_query(self, text):
        return self._embed(text)

    def _embed(self, text):
        vector = [0.0] * self.dimensions
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if not norm:  # pgvector cannot take a cosine distance to the zero vector
            vector[0], norm = 1.0, 1.0
        return [value / norm for value in vector]


register_embedder("fastembed", FastEmbedEmbedder)
register_embedder("hashing", HashingEmbedder)
