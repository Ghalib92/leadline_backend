import pytest


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def user(db):
    from apps.authentication.tests.factories import UserFactory

    return UserFactory()


@pytest.fixture
def auth_client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture(autouse=True)
def web(monkeypatch):
    """No test reaches the network: agent HTTP goes to a fake web (register routes on
    it), and every host resolves to a public example address."""
    import httpx

    from apps.ai.testing import Web
    from apps.ai.tools import http

    fake = Web()
    monkeypatch.setattr(http, "transport", httpx.MockTransport(fake))
    monkeypatch.setattr(http, "resolve_host", lambda host: ["93.184.216.34"])
    return fake


# ---------------------------------------------------------------------------
# Temporal's test servers (tests marked `temporal`). The SDK downloads them once, then
# they are cached; without internet the tests that need them are skipped.
# ---------------------------------------------------------------------------


def _temporal_server(start):
    """A Temporal server on its own event-loop thread, so test code stays synchronous
    Django code, like a request starting a job."""
    import asyncio
    import threading
    from types import SimpleNamespace

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    def call(coro, timeout=120):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    try:
        env = call(start())
    except Exception as exc:  # no binary and no internet to fetch it
        loop.call_soon_threadsafe(loop.stop)
        pytest.skip(f"Temporal test server unavailable: {exc}")
    yield SimpleNamespace(
        client=env.client, call=call, address=env.client.service_client.config.target_host
    )
    call(env.shutdown())
    loop.call_soon_threadsafe(loop.stop)


@pytest.fixture(scope="session")
def temporal():
    """The light test server (time skipping): workflows and workers, no Schedules."""
    from temporalio.testing import WorkflowEnvironment

    yield from _temporal_server(WorkflowEnvironment.start_time_skipping)


@pytest.fixture(scope="session")
def temporal_dev():
    """The full dev server, as `make temporal-dev` runs it: Schedules too."""
    from temporalio.testing import WorkflowEnvironment

    yield from _temporal_server(WorkflowEnvironment.start_local)


@pytest.fixture
def temporal_queue(temporal, settings):
    """A task queue of this test's own, so workers of other tests never take its work."""
    import uuid

    settings.TEMPORAL_TASK_QUEUE = temporal.queue = f"test-{uuid.uuid4().hex[:8]}"
    return temporal.queue


@pytest.fixture
def temporal_worker(temporal, temporal_queue, settings):
    """The real worker (config.temporal) against the test server, with jobs going to it
    rather than running inline."""
    import asyncio

    from config.temporal import build_worker

    settings.JOBS_RUN_INLINE = False
    settings.TEMPORAL_ADDRESS = temporal.address

    async def serve():
        worker = build_worker(temporal.client)
        asyncio.ensure_future(worker.run())
        return worker

    worker = temporal.call(serve())
    yield worker
    temporal.call(worker.shutdown())


def wait_for(check, *, seconds=60):
    """Poll `check()` until it is truthy (a background worker is doing the work)."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.2)
    return False

