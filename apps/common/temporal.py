"""Talking to Temporal from Django, and running Django code inside Temporal activities.

- `connect` / `run`: Django code is synchronous; `run` calls an async Temporal function
  from it with a fresh connection, which is cheap at our volume and safe from any thread.
- `in_thread` / `heartbeating`: activities run the synchronous work (ORM, LangGraph) in a
  thread, so the worker's event loop stays free to send heartbeats.
"""

import asyncio

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import connections
from temporalio import activity
from temporalio.client import Client

CONNECT_TIMEOUT_SECONDS = 5
# Under the workflows' heartbeat timeout (a minute), with room for a slow network.
HEARTBEAT_EVERY_SECONDS = 10


async def connect() -> Client:
    return await asyncio.wait_for(
        Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE),
        timeout=CONNECT_TIMEOUT_SECONDS,
    )


def run(fn, *args, **kwargs):
    """Call `await fn(client, *args, **kwargs)` from synchronous code."""

    async def call():
        return await fn(await connect(), *args, **kwargs)

    return async_to_sync(call)()


async def in_thread(fn, *args, **kwargs):
    """Run `fn` in a worker thread with its own database connection, closed afterwards
    (one connection per activity; threads outlive it)."""

    def call():
        try:
            return fn(*args, **kwargs)
        finally:
            connections.close_all()

    return await asyncio.to_thread(call)


async def heartbeating(fn, *args, **kwargs):
    """`in_thread`, sending a heartbeat every few seconds until `fn` returns. When the
    heartbeats stop (the worker died), Temporal retries the activity elsewhere."""
    work = asyncio.ensure_future(in_thread(fn, *args, **kwargs))
    while True:
        done, _ = await asyncio.wait({work}, timeout=HEARTBEAT_EVERY_SECONDS)
        if done:
            return work.result()
        activity.heartbeat()
