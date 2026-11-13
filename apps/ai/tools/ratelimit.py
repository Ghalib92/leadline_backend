"""A per-minute request limit shared through the Django cache.

With a shared cache (Redis) the limit holds across workers; with the default
per-process cache it holds per worker process.
"""

import time

from django.core.cache import cache


class RateLimited(Exception):
    """The limit was reached and waiting for the next minute would take too long."""


class MinuteRateLimiter:
    def __init__(self, name: str, per_minute: int, *, clock=time.time, sleep=time.sleep):
        self.name = name
        self.per_minute = per_minute
        self.clock = clock
        self.sleep = sleep

    def acquire(self, *, max_wait: float = 60.0) -> None:
        """Take one request slot, waiting for the next minute if needed."""
        deadline = self.clock() + max_wait
        while True:
            now = self.clock()
            minute = int(now // 60)
            key = f"ratelimit:{self.name}:{minute}"
            cache.add(key, 0, timeout=120)
            try:
                count = cache.incr(key)
            except ValueError:  # expired between add and incr
                continue
            if count <= self.per_minute:
                return
            wait = (minute + 1) * 60 - now + 0.05
            if now + wait > deadline:
                raise RateLimited(f"{self.name}: {self.per_minute} requests per minute reached.")
            self.sleep(wait)
