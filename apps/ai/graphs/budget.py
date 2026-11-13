"""Per-run limits on searches, pages, model tokens and wall time.

Every node asks before spending. Running out raises BudgetExhausted, and the run ends
as "partial" with what it found, never as a crash:

- searches or pages ("soft"): no more searching or reading, but what was already
  found is still scored and saved;
- tokens or wall time ("hard"): no more model calls; what was already scored is saved.

Thread-safe, as pages are read and scored in parallel.
"""

import threading
import time


class BudgetExhausted(Exception):
    """A limit was reached. The message is shown to the user."""

    def __init__(self, message: str, *, hard: bool):
        super().__init__(message)
        self.hard = hard


class Budget:
    # Tokens kept back so a model call is not started when it would surely overrun.
    TOKEN_RESERVE = 3000

    def __init__(self, limits: dict, *, clock=time.monotonic):
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.searches = 0
        self.pages = 0
        self.tokens = 0
        self._lock = threading.Lock()

    def seconds_left(self) -> float:
        return self.limits["max_wall_seconds"] - (self.clock() - self.started)

    def check_time(self) -> None:
        if self.seconds_left() <= 0:
            raise BudgetExhausted(
                f"Time limit reached ({self.limits['max_wall_seconds']} seconds).", hard=True
            )

    def searches_left(self) -> int:
        return max(0, self.limits["max_searches"] - self.searches)

    def use_search(self) -> None:
        with self._lock:
            self.check_time()
            if self.searches >= self.limits["max_searches"]:
                raise BudgetExhausted(
                    f"Search limit reached ({self.limits['max_searches']} searches).", hard=False
                )
            self.searches += 1

    def use_page(self) -> None:
        with self._lock:
            self.check_time()
            if self.pages >= self.limits["max_pages"]:
                raise BudgetExhausted(
                    f"Page limit reached ({self.limits['max_pages']} pages).", hard=False
                )
            self.pages += 1

    def check_tokens(self) -> None:
        self.check_time()
        if self.tokens + self.TOKEN_RESERVE > self.limits["max_tokens"]:
            raise BudgetExhausted(
                f"Token limit reached ({self.limits['max_tokens']} tokens).", hard=True
            )

    def add_tokens(self, usage: dict) -> None:
        total = usage.get("total_tokens") or (
            (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        )
        with self._lock:
            self.tokens += int(total or 0)

    def usage(self) -> dict:
        return {
            "searches": self.searches,
            "pages": self.pages,
            "tokens": self.tokens,
            "seconds": round(self.clock() - self.started, 1),
        }
