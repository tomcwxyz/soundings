"""Tests for CaptureMiddleware.

These tests build a minimal FastAPI app with a fake tool route so we
exercise the middleware in isolation — without spinning up the real
adapter registry / geography service from soundings.app.
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from soundings.capture.context import CaptureContext
from soundings.capture.middleware import CaptureMiddleware
from soundings.http.session import SessionMiddleware


class FakeRawWriter:
    """Records the CaptureContexts it receives without writing to a DB."""

    def __init__(self) -> None:
        self.calls: list[CaptureContext] = []
        self._next_id = uuid4()

    async def write(self, ctx: CaptureContext) -> UUID | None:
        self.calls.append(ctx)
        return self._next_id


class FakeSanitiser:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[UUID] = []
        self.delay = delay

    async def sanitise(self, record_id: UUID) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append(record_id)


@pytest.fixture
def app_with_fake_tool() -> tuple[FastAPI, FakeRawWriter]:
    app = FastAPI()
    writer = FakeRawWriter()
    app.state.raw_writer = writer

    # Outer-most middleware runs last on request, first on response, so
    # CaptureMiddleware (added second) wraps SessionMiddleware (added first).
    app.add_middleware(CaptureMiddleware)
    app.add_middleware(SessionMiddleware)

    @app.post("/v1/tools/find_place")
    async def find_place(body: dict[str, Any]) -> dict[str, Any]:
        # Mimic the shape `find_place` returns; nl_question must NOT be in
        # body by the time the handler sees it (middleware pops it).
        assert "nl_question" not in body, "middleware should have stripped nl_question"
        return {
            "matches": [
                {
                    "id": "ltla24:E06000004",
                    "name": "Stockton-on-Tees",
                    "type": "ltla24",
                    "parent_ids": ["region:E12000001"],
                    "confidence": 1.0,
                }
            ],
            "sources": [],
        }

    return app, writer


async def test_middleware_extracts_nl_question_and_invokes_writer(
    app_with_fake_tool: tuple[FastAPI, FakeRawWriter],
) -> None:
    app, writer = app_with_fake_tool
    session_id = uuid4()
    transport = httpx.ASGITransport(app=app)
    cookies = {
        "soundings_session": str(session_id),
        "soundings_consent": "full",
        "soundings_sector": "researcher",
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        response = await client.post(
            "/v1/tools/find_place",
            json={
                "query": "Stockton-on-Tees",
                "nl_question": "What's the population of Stockton?",
            },
        )

    assert response.status_code == 200
    assert len(writer.calls) == 1
    ctx = writer.calls[0]
    assert ctx.tool_called == "find_place"
    assert ctx.session_id == session_id
    assert ctx.consent_level == "full"
    assert ctx.asker_sector == "researcher"
    assert ctx.natural_language_question == "What's the population of Stockton?"
    assert "nl_question" not in ctx.tool_inputs


async def test_middleware_captures_ask_query_and_sse_sources() -> None:
    """The natural-language Ask route should produce a corpus record too."""
    app = FastAPI()
    writer = FakeRawWriter()
    app.state.raw_writer = writer
    app.add_middleware(CaptureMiddleware)
    app.add_middleware(SessionMiddleware)

    @app.post("/v1/ask")
    async def ask(body: dict[str, Any]) -> StreamingResponse:
        assert body["query"] == "How is Stockton doing?"

        async def events() -> Any:
            yield (
                'data: {"type":"sources","sources":'
                '[{"source_id":"ons.aps"}]}\n\n'
            )
            yield 'data: {"type":"done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    session_id = uuid4()
    transport = httpx.ASGITransport(app=app)
    cookies = {
        "soundings_session": str(session_id),
        "soundings_consent": "full",
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        response = await client.post(
            "/v1/ask",
            json={"query": "How is Stockton doing?"},
        )

    assert response.status_code == 200
    assert len(writer.calls) == 1
    ctx = writer.calls[0]
    assert ctx.tool_called == "ask"
    assert ctx.session_id == session_id
    assert ctx.natural_language_question == "How is Stockton doing?"
    assert ctx.sources_used == ["ons.aps"]
    assert ctx.result_status == "ok"


async def test_middleware_discards_nl_question_under_non_full_consent(
    app_with_fake_tool: tuple[FastAPI, FakeRawWriter],
) -> None:
    app, writer = app_with_fake_tool
    transport = httpx.ASGITransport(app=app)
    cookies = {
        "soundings_session": str(uuid4()),
        "soundings_consent": "minimal",
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        await client.post(
            "/v1/tools/find_place",
            json={"query": "Stockton", "nl_question": "Should be discarded"},
        )

    ctx = writer.calls[0]
    assert ctx.consent_level == "minimal"
    assert ctx.natural_language_question is None


async def test_middleware_skips_capture_when_consent_is_none(
    app_with_fake_tool: tuple[FastAPI, FakeRawWriter],
) -> None:
    app, writer = app_with_fake_tool
    transport = httpx.ASGITransport(app=app)
    cookies = {
        "soundings_session": str(uuid4()),
        "soundings_consent": "none",
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        response = await client.post("/v1/tools/find_place", json={"query": "Stockton"})

    assert response.status_code == 200
    assert writer.calls == []


async def test_middleware_skips_non_tool_routes(
    app_with_fake_tool: tuple[FastAPI, FakeRawWriter],
) -> None:
    app, writer = app_with_fake_tool

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    transport = httpx.ASGITransport(app=app)
    cookies = {
        "soundings_session": str(uuid4()),
        "soundings_consent": "full",
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        await client.get("/healthz")

    assert writer.calls == []


async def test_middleware_works_without_session_middleware_upstream() -> None:
    """CaptureMiddleware must parse cookies itself.

    Guards against quietly degrading to anonymous capture if SessionMiddleware
    isn't in the stack — e.g. if a future refactor swaps middleware order
    or someone wires a partial test app.
    """
    app = FastAPI()
    writer = FakeRawWriter()
    app.state.raw_writer = writer
    # Note: only CaptureMiddleware; SessionMiddleware deliberately omitted.
    app.add_middleware(CaptureMiddleware)

    @app.post("/v1/tools/find_place")
    async def find_place(body: dict[str, Any]) -> dict[str, Any]:
        return {"matches": [], "sources": []}

    transport = httpx.ASGITransport(app=app)
    cookies = {
        "soundings_session": str(uuid4()),
        "soundings_consent": "full",
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        await client.post("/v1/tools/find_place", json={"query": "Stockton"})

    assert len(writer.calls) == 1
    assert writer.calls[0].consent_level == "full"


async def test_middleware_schedules_sanitiser_after_raw_write() -> None:
    """Successful capture should fire the sanitiser via create_task."""
    app = FastAPI()
    writer = FakeRawWriter()
    sanitiser = FakeSanitiser()
    app.state.raw_writer = writer
    app.state.sanitiser_worker = sanitiser
    app.state.background_tasks = set[asyncio.Task[None]]()
    app.add_middleware(CaptureMiddleware)
    app.add_middleware(SessionMiddleware)

    @app.post("/v1/tools/find_place")
    async def find_place(body: dict[str, Any]) -> dict[str, Any]:
        del body
        return {"matches": [], "sources": []}

    transport = httpx.ASGITransport(app=app)
    cookies = {"soundings_session": str(uuid4()), "soundings_consent": "minimal"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        await client.post("/v1/tools/find_place", json={"query": "Stockton"})

    # The sanitiser task may not have run by the time the HTTP response
    # came back. Give the event loop a tick to drain.
    while app.state.background_tasks:
        await asyncio.sleep(0)

    assert len(sanitiser.calls) == 1
    assert sanitiser.calls[0] == writer._next_id


async def test_background_tasks_set_is_populated_then_drained() -> None:
    """Strong reference must exist between scheduling and completion."""
    app = FastAPI()
    writer = FakeRawWriter()
    sanitiser = FakeSanitiser(delay=0.05)
    app.state.raw_writer = writer
    app.state.sanitiser_worker = sanitiser
    app.state.background_tasks = set[asyncio.Task[None]]()
    app.add_middleware(CaptureMiddleware)
    app.add_middleware(SessionMiddleware)

    @app.post("/v1/tools/find_place")
    async def find_place(body: dict[str, Any]) -> dict[str, Any]:
        del body
        return {"matches": [], "sources": []}

    transport = httpx.ASGITransport(app=app)
    cookies = {"soundings_session": str(uuid4()), "soundings_consent": "minimal"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        await client.post("/v1/tools/find_place", json={"query": "Stockton"})
        # Sanitiser is sleeping 50ms; task must be tracked.
        assert len(app.state.background_tasks) == 1
        await asyncio.sleep(0.1)

    # Once it finishes, the discard callback removes it from the set.
    assert app.state.background_tasks == set()
    assert len(sanitiser.calls) == 1


class AlwaysDowngradeLimiter:
    async def should_downgrade(self, session_id: UUID) -> bool:
        del session_id
        return True


async def test_rate_limit_downgrades_full_consent_silently() -> None:
    """A session over the per-hour cap should land its record at minimal."""
    app = FastAPI()
    writer = FakeRawWriter()
    app.state.raw_writer = writer
    app.state.rate_limiter = AlwaysDowngradeLimiter()
    app.add_middleware(CaptureMiddleware)
    app.add_middleware(SessionMiddleware)

    @app.post("/v1/tools/find_place")
    async def find_place(body: dict[str, Any]) -> dict[str, Any]:
        del body
        return {"matches": [], "sources": []}

    transport = httpx.ASGITransport(app=app)
    cookies = {
        "soundings_session": str(uuid4()),
        "soundings_consent": "full",
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        response = await client.post(
            "/v1/tools/find_place",
            json={"query": "Stockton", "nl_question": "should be dropped"},
        )

    # Tool call still 200 — no error visible to the asker.
    assert response.status_code == 200
    assert len(writer.calls) == 1
    ctx = writer.calls[0]
    # capture_level downgraded, nl_question discarded.
    assert ctx.consent_level == "minimal"
    assert ctx.natural_language_question is None


async def test_middleware_short_circuits_when_no_writer_configured() -> None:
    """Without a writer the middleware must not touch body or response.

    Performance guard: drainage + buffering shouldn't run for routes that
    aren't going to be captured.
    """
    app = FastAPI()
    # No raw_writer on app.state — capture is dormant.
    app.add_middleware(CaptureMiddleware)
    app.add_middleware(SessionMiddleware)

    body_received_by_handler: dict[str, Any] = {}

    @app.post("/v1/tools/find_place")
    async def find_place(body: dict[str, Any]) -> dict[str, Any]:
        body_received_by_handler.update(body)
        return {"matches": [], "sources": []}

    transport = httpx.ASGITransport(app=app)
    cookies = {
        "soundings_session": str(uuid4()),
        "soundings_consent": "full",
    }
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        await client.post(
            "/v1/tools/find_place",
            json={"query": "Stockton", "nl_question": "passes straight through"},
        )

    # Without a writer the middleware short-circuits before stripping
    # nl_question — the handler sees the original body. Tools accept the
    # extra field thanks to Pydantic's default extra="ignore".
    assert body_received_by_handler == {
        "query": "Stockton",
        "nl_question": "passes straight through",
    }
