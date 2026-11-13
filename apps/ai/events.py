"""Progress events from agent runs; the run timeline in the UI is built from them.

Graph nodes and tools receive a RunEventEmitter and call emit() for every step and
tool call ("Searching grants.gov for…", "Reading worldbank.org/…").
apps.discovery.recorder.RunEventRecorder persists them for discovery runs; the null
and list emitters cover scripts and tests.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from django.utils import timezone

# Step keys of a discovery run, matching the frontend's run schema.
DISCOVERY_STEPS = ("profile", "search", "read", "match", "save")

Level = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class RunEvent:
    step: str
    message: str
    level: Level = "info"
    tool: str = ""
    source: str = ""  # source name or domain shown in the UI, e.g. "grants.gov"
    url: str = ""  # the page being searched or read, when there is one
    data: Mapping[str, object] = field(default_factory=dict)
    at: datetime = field(default_factory=timezone.now)


class RunEventEmitter(Protocol):
    def emit(self, event: RunEvent) -> None: ...


class NullEmitter:
    def emit(self, event: RunEvent) -> None:
        pass


class ListEmitter:
    """Collects events in memory, for tests and dry runs."""

    def __init__(self):
        self.events: list[RunEvent] = []

    def emit(self, event: RunEvent) -> None:
        self.events.append(event)
