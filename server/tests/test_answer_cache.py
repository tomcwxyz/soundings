"""Unit tests for the AnswerCacheStore and orchestrator cache integration.

Uses a fake cache store (in-memory dict) to test the orchestrator
integration without needing a real database or aiosqlite dependency.
The hash/normalisation tests are pure functions and need no fixture.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soundings.ask.dispatcher import ToolDispatcher
from soundings.ask.orchestrator import AskOrchestrator
from soundings.ask.prompts import SystemPromptBuilder
from soundings.cache.answer_cache import CachedAnswer, question_hash

# ── Hash tests (pure functions) ─────────────────────────────────────────


def test_question_hash_is_deterministic():
    """Same question + place_id → same hash."""
    h1 = question_hash("Summarise Stockton", "ltla24:E06000004")
    h2 = question_hash("Summarise Stockton", "ltla24:E06000004")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_question_hash_normalises_whitespace():
    """Whitespace + case differences should not change the hash."""
    h1 = question_hash("Summarise   Stockton")
    h2 = question_hash(" summarise stockton ")
    assert h1 == h2


def test_question_hash_differs_by_place_id():
    """Same question with different place_id → different hash."""
    h1 = question_hash("Summarise", place_id="ltla24:A")
    h2 = question_hash("Summarise", place_id="ltla24:B")
    assert h1 != h2


def test_question_hash_none_vs_empty_place():
    """None and empty-string place_id should hash the same."""
    h1 = question_hash("Hello", place_id=None)
    h2 = question_hash("Hello", place_id="")
    assert h1 == h2


def test_question_hash_differs_for_different_questions():
    """Different questions → different hashes."""
    h1 = question_hash("Summarise Stockton")
    h2 = question_hash("Summarise Middlesbrough")
    assert h1 != h2


# ── Fake cache store ────────────────────────────────────────────────────


class FakeAnswerCacheStore:
    """In-memory answer cache that mimics AnswerCacheStore's interface."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self.get_calls: list[tuple[str, str | None]] = []
        self.put_calls: list[
            tuple[str, str | None, list[dict[str, Any]], list[dict[str, Any]] | None]
        ] = []

    async def get(self, question: str, place_id: str | None = None) -> CachedAnswer | None:
        self.get_calls.append((question, place_id))
        qhash = question_hash(question, place_id)
        entry = self._store.get(qhash)
        if entry is None:
            return None
        if datetime.now(tz=UTC) >= entry["expires_at"]:
            return None
        entry["hit_count"] += 1
        return CachedAnswer(events=list(entry["events"]), messages=entry.get("messages"))

    async def put(
        self,
        question: str,
        place_id: str | None,
        events: list[dict[str, Any]],
        messages: list[dict[str, Any]] | None = None,
        *,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        self.put_calls.append((question, place_id, events, messages))
        qhash = question_hash(question, place_id)
        self._store[qhash] = {
            "events": list(events),
            "messages": list(messages) if messages is not None else None,
            "expires_at": datetime.now(tz=UTC) + ttl,
            "hit_count": 0,
        }

    async def invalidate(self, question: str, place_id: str | None = None) -> None:
        qhash = question_hash(question, place_id)
        self._store.pop(qhash, None)


@pytest.fixture
def fake_cache():
    return FakeAnswerCacheStore()


# ── Fake cache store tests ──────────────────────────────────────────────


async def test_fake_cache_miss_returns_none(fake_cache):
    result = await fake_cache.get("anything")
    assert result is None


async def test_fake_cache_put_then_get(fake_cache):
    events = [{"type": "done"}]
    await fake_cache.put("Stockton", None, events)
    result = await fake_cache.get("Stockton", None)
    assert result is not None
    assert result.events == events
    assert result.messages is None


async def test_fake_cache_overwrites(fake_cache):
    await fake_cache.put("Stockton", None, [{"type": "done"}])
    await fake_cache.put("Stockton", None, [{"type": "block", "block": {}}])
    result = await fake_cache.get("Stockton", None)
    assert len(result.events) == 1
    assert result.events[0]["type"] == "block"


# ── Orchestrator integration tests ──────────────────────────────────────


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeToolUseBlock:
    def __init__(self, block_id: str, name: str, input_dict: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.id = block_id
        self.name = name
        self.input = input_dict


class _FakeResponse:
    def __init__(self, content: list[Any], stop_reason: str = "tool_use") -> None:
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._call_idx = 0
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._call_idx >= len(self._responses):
            raise RuntimeError("No more fake responses")
        resp = self._responses[self._call_idx]
        self._call_idx += 1
        return resp


class _FakeAnthropic:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _FakeMessages(responses)


def _make_fake_state() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        geography_service=MagicMock(),
        orchestrator=MagicMock(),
        engine=MagicMock(),
    )


def _collect_events() -> tuple[list[dict[str, Any]], Any]:
    events: list[dict[str, Any]] = []

    def callback(event: dict[str, Any]) -> None:
        events.append(event)

    return events, callback


async def test_orchestrator_caches_answer_after_run(fake_cache):
    """After a successful run, the orchestrator should store events in the cache."""
    events, cb = _collect_events()
    state = _make_fake_state()
    dispatcher = ToolDispatcher(state)
    prompt_builder = SystemPromptBuilder()

    responses = [
        _FakeResponse(
            [
                _FakeToolUseBlock(
                    "tool_1", "compose_answer", {"blocks": [{"type": "text", "markdown": "Hi"}]}
                )
            ]
        ),
    ]

    fake_client = _FakeAnthropic(responses)
    with patch("soundings.ask.orchestrator.get_anthropic_client", return_value=fake_client):
        orch = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=prompt_builder,
            api_key="fake-key",
            model="claude-sonnet-5",
            answer_cache=fake_cache,
        )
        await orch.run("Summarise Stockton", cb)

    # Should have emitted events
    assert any(e["type"] == "done" for e in events)

    # Should have cached the events
    assert len(fake_cache.put_calls) == 1
    cached_question, cached_place_id, cached_events, cached_messages = fake_cache.put_calls[0]
    assert cached_question == "Summarise Stockton"
    assert cached_place_id is None
    assert any(e["type"] == "done" for e in cached_events)
    assert cached_messages is not None
    assert cached_messages[0]["role"] == "user"


async def test_orchestrator_replays_cached_answer_without_claude(fake_cache):
    """On a cache hit, the orchestrator should replay events without calling Claude."""
    # Pre-populate the cache
    cached_events = [
        {"type": "status", "message": "Calling find_place…"},
        {"type": "block", "block": {"type": "text", "markdown": "Cached answer!"}},
        {"type": "sources", "sources": []},
        {"type": "done"},
    ]
    cached_messages = [{"role": "user", "content": "Summarise Stockton"}]
    await fake_cache.put("Summarise Stockton", None, cached_events, messages=cached_messages)
    # Clear the call log — we only want to track orchestrator-triggered calls
    fake_cache.put_calls.clear()

    events, cb = _collect_events()
    state = _make_fake_state()
    dispatcher = ToolDispatcher(state)
    prompt_builder = SystemPromptBuilder()

    # If Claude is called, this will raise (no responses queued)
    fake_client = _FakeAnthropic([])

    with patch("soundings.ask.orchestrator.get_anthropic_client", return_value=fake_client):
        orch = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=prompt_builder,
            api_key="fake-key",
            model="claude-sonnet-5",
            answer_cache=fake_cache,
        )
        returned_messages = await orch.run("Summarise Stockton", cb)

    # Should have replayed the cached events
    assert len(events) == 4
    assert events[0]["type"] == "status"
    assert events[1]["block"]["markdown"] == "Cached answer!"
    assert events[3]["type"] == "done"

    # Claude should NOT have been called
    assert len(fake_client.messages.calls) == 0
    assert returned_messages == cached_messages

    # Cache should have been checked
    assert len(fake_cache.get_calls) == 1
    # But NOT re-stored (no put on a hit)
    assert len(fake_cache.put_calls) == 0


async def test_orchestrator_cache_read_failure_falls_back_to_claude(fake_cache):
    """A broken cache must not make the Ask endpoint unavailable."""
    fake_cache.get = AsyncMock(side_effect=RuntimeError("cache unavailable"))

    events, cb = _collect_events()
    state = _make_fake_state()
    dispatcher = ToolDispatcher(state)
    prompt_builder = SystemPromptBuilder()
    fake_client = _FakeAnthropic(
        [
            _FakeResponse(
                [
                    _FakeToolUseBlock(
                        "tool_1",
                        "compose_answer",
                        {"blocks": [{"type": "text", "markdown": "Fresh answer"}]},
                    )
                ]
            )
        ]
    )

    with patch("soundings.ask.orchestrator.get_anthropic_client", return_value=fake_client):
        orch = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=prompt_builder,
            api_key="fake-key",
            model="claude-sonnet-5",
            answer_cache=fake_cache,
        )
        await orch.run("Summarise Stockton", cb)

    assert len(fake_client.messages.calls) == 1
    assert any(
        e.get("type") == "block" and e.get("block", {}).get("markdown") == "Fresh answer"
        for e in events
    )


async def test_orchestrator_ignores_legacy_cache_without_messages(fake_cache):
    """Legacy event-only cache entries cannot seed a follow-up conversation."""
    await fake_cache.put("Summarise Stockton", None, [{"type": "done"}])

    events, cb = _collect_events()
    state = _make_fake_state()
    dispatcher = ToolDispatcher(state)
    prompt_builder = SystemPromptBuilder()
    fake_client = _FakeAnthropic(
        [
            _FakeResponse(
                [
                    _FakeToolUseBlock(
                        "tool_1",
                        "compose_answer",
                        {"blocks": [{"type": "text", "markdown": "Fresh answer"}]},
                    )
                ]
            )
        ]
    )

    with patch("soundings.ask.orchestrator.get_anthropic_client", return_value=fake_client):
        orch = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=prompt_builder,
            api_key="fake-key",
            model="claude-sonnet-5",
            answer_cache=fake_cache,
        )
        await orch.run("Summarise Stockton", cb)

    assert len(fake_client.messages.calls) == 1
    assert any(e.get("type") == "block" for e in events)


async def test_orchestrator_does_not_cache_errors(fake_cache):
    """If the run produces an error (no 'done' event), don't cache."""
    events, cb = _collect_events()
    state = _make_fake_state()
    dispatcher = ToolDispatcher(state)
    prompt_builder = SystemPromptBuilder()

    # Claude loops forever → max iterations → error
    infinite_responses = [
        _FakeResponse([_FakeToolUseBlock(f"tool_{i}", "find_place", {"query": "x"})])
        for i in range(10)
    ]

    fake_client = _FakeAnthropic(infinite_responses)
    with patch("soundings.ask.orchestrator.get_anthropic_client", return_value=fake_client):
        orch = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=prompt_builder,
            api_key="fake-key",
            model="claude-sonnet-5",
            answer_cache=fake_cache,
            max_iterations=2,
        )
        await orch.run("bad question", cb)

    # Should have an error event
    assert any(e["type"] == "error" for e in events)

    # Should NOT have cached anything (no 'done' event)
    assert len(fake_cache.put_calls) == 0


async def test_orchestrator_without_cache_works_normally():
    """Orchestrator with no answer_cache should behave as before."""
    events, cb = _collect_events()
    state = _make_fake_state()
    dispatcher = ToolDispatcher(state)
    prompt_builder = SystemPromptBuilder()

    responses = [
        _FakeResponse(
            [
                _FakeToolUseBlock(
                    "tool_1", "compose_answer", {"blocks": [{"type": "text", "markdown": "Hi"}]}
                )
            ]
        ),
    ]

    fake_client = _FakeAnthropic(responses)
    with patch("soundings.ask.orchestrator.get_anthropic_client", return_value=fake_client):
        orch = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=prompt_builder,
            api_key="fake-key",
            model="claude-sonnet-5",
            # No answer_cache — should work exactly as before
        )
        await orch.run("Stockton", cb)

    assert any(e["type"] == "done" for e in events)


async def test_orchestrator_cache_hit_with_place_id(fake_cache):
    """Cache key should include place_id — same question at different place = different cache entry."""
    # Cache an answer for Stockton
    await fake_cache.put("Summarise", "ltla24:E06000004", [{"type": "done"}])
    # Clear the call log — we only want to track orchestrator-triggered calls
    fake_cache.put_calls.clear()

    _events, cb = _collect_events()
    state = _make_fake_state()
    dispatcher = ToolDispatcher(state)
    prompt_builder = SystemPromptBuilder(place_id="ltla24:E08000015")  # Different place

    fake_client = _FakeAnthropic(
        [
            _FakeResponse(
                [
                    _FakeToolUseBlock(
                        "t1",
                        "compose_answer",
                        {"blocks": [{"type": "text", "markdown": "New answer"}]},
                    )
                ]
            ),
        ]
    )

    with patch("soundings.ask.orchestrator.get_anthropic_client", return_value=fake_client):
        orch = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=prompt_builder,
            api_key="fake-key",
            model="claude-sonnet-5",
            answer_cache=fake_cache,
        )
        await orch.run("Summarise", cb)

    # Should have called Claude (cache miss — different place_id)
    assert len(fake_client.messages.calls) == 1
    # Should have stored a new entry
    assert len(fake_cache.put_calls) == 1
    assert fake_cache.put_calls[0][1] == "ltla24:E08000015"
