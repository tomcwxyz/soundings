"""Unit tests for get_observations integration into the ask dispatcher.

Per Task 9 of the observations MVP plan
(docs/plans/2026-08-24-observations-mvp.md). Verifies the dispatcher
registers the handler and exposes the tool spec so the /v1/ask LLM can call
get_observations to surface experiential evidence alongside official data.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from soundings.ask.dispatcher import ToolDispatcher

# --- Fake service objects -------------------------------------------------


class _FakeConn:
    """Minimal async connection that returns canned observation rows."""

    def __init__(self) -> None:
        self._scalar_call = False

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql_text = str(statement)
        # Per-theme summary aggregate for a place.
        if "GROUP BY o.theme" in sql_text:
            return _FakeResult(
                rows=[
                    SimpleNamespace(
                        theme="food",
                        count=1,
                        latest_submission=__import__("datetime").datetime(2026, 8, 1, 12, 0, 0),
                        organisation_names=["Food Aid UK"],
                    )
                ]
            )
        # Main observation SELECT.
        return _FakeResult(
            rows=[
                SimpleNamespace(
                    id=__import__("uuid").UUID(int=1),
                    organisation_id="org-1",
                    organisation_name="Food Aid UK",
                    place_id="ltla24:E06000047",
                    place_name="Newcastle upon Tyne",
                    period_start=__import__("datetime").date(2026, 7, 1),
                    period_end=None,
                    theme="food",
                    statement="Demand at our food bank has risen sharply.",
                    indicator_key=None,
                    value=None,
                    unit=None,
                    evidence_type="qualitative",
                    methodology_note=None,
                    confidence="medium",
                    submitted_at=__import__("datetime").datetime(2026, 8, 1, 12, 0, 0),
                )
            ]
        )

    async def scalar(self, statement: Any, params: Any = None) -> Any:
        # Observation count query — always return 1 for the single canned row.
        return 1


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeEngine:
    def connect(self) -> _FakeConn:
        return _FakeConn()


def _make_dispatcher() -> ToolDispatcher:
    state = SimpleNamespace(
        geography_service=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        engine=FakeEngine(),
    )
    return ToolDispatcher(state)


# --- Tests -----------------------------------------------------------------


def test_dispatcher_has_get_observations_handler() -> None:
    """get_observations is in _handlers so dispatch() can route to it."""
    dispatcher = _make_dispatcher()
    handlers = dispatcher._handlers
    assert "get_observations" in handlers
    assert callable(handlers["get_observations"])


def test_dispatcher_includes_get_observations_tool_spec() -> None:
    """get_observations_spec() is in the tool catalogue the LLM sees."""
    dispatcher = _make_dispatcher()
    specs = dispatcher.tool_specs()
    names = [s["name"] for s in specs]
    assert "get_observations" in names
    obs_spec = next(s for s in specs if s["name"] == "get_observations")
    assert "description" in obs_spec
    assert "input_schema" in obs_spec


@pytest.mark.asyncio
async def test_dispatcher_dispatch_get_observations() -> None:
    """Dispatching get_observations returns the observations payload."""
    dispatcher = _make_dispatcher()
    result = await dispatcher.dispatch(
        "get_observations",
        {"place_id": "ltla24:E06000047", "limit": 10},
    )
    assert isinstance(result, dict)
    assert "observations" in result
    assert "total" in result
    assert "summary" in result
    assert result["total"] == 1
    assert len(result["observations"]) == 1
    assert result["observations"][0]["organisation_name"] == "Food Aid UK"
    assert result["summary"] is not None
    assert result["summary"]["total_observations"] == 1


@pytest.mark.asyncio
async def test_dispatcher_dispatch_get_observations_validates_input() -> None:
    """An invalid limit surfaces a ValidationError."""
    from pydantic import ValidationError

    dispatcher = _make_dispatcher()
    with pytest.raises(ValidationError):
        await dispatcher.dispatch("get_observations", {"limit": 0})
