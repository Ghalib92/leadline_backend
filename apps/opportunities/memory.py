"""Decision memory: reviews as embeddings, so similar past decisions can be found."""

from apps.knowledge.embeddings import get_embedder

# The snapshot fields that describe what was decided on.
MEMORY_FIELDS = ("title", "funder", "opportunity_type", "summary", "eligibility", "location")


def memory_text(facts: dict) -> str:
    """One line per field; the same function embeds reviews and new candidates."""
    return "\n".join(f"{field}: {facts[field]}" for field in MEMORY_FIELDS if facts.get(field))[
        :4000
    ]


def embed_facts(facts: dict) -> list[float]:
    return get_embedder().embed_query(memory_text(facts))
