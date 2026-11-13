"""LangSmith tracing, behind the LANGSMITH_TRACING flag.

LangChain and LangGraph read the LANGSMITH_* environment variables, so enabling is
just exporting them from settings when the app starts.
"""

import os

from django.conf import settings


def configure_tracing() -> bool:
    enabled = bool(settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY)
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    if enabled:
        os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
    return enabled


def run_config(name: str, *, metadata: dict, tags: list[str]) -> dict:
    """Config for a top-level graph invocation, so traces are findable by run id."""
    return {"run_name": name, "metadata": metadata, "tags": ["leadline", *tags]}
