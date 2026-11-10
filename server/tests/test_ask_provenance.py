"""Every Ask answer carries the sources its numbers came from.

The 9 November 2026 baseline found every final answer shipped an empty
source list: the dispatcher declared ``_sources`` but nothing ever added to
it. It also found figures in answers that no tool had returned. These tests
drive the real dispatcher and loop with a fake model.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from soundings.ask.dispatcher import ToolDispatcher
from soundings.ask.orchestrator import AskOrchestrator
from soundings.ask.prompts import SystemPromptBuilder
from soundings.contracts.indicator_value import IndicatorValue
from soundings.contracts.source_ref import SourceRef
from soundings.tools.get_indicators import GetIndicatorsOutput

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)


def _source(source_id: str, retrieved_at: datetime = NOW) -> SourceRef:
    return SourceRef(
        source_id=source_id,
        source_label=source_id,
        publisher="DfE",
        retrieved_at=retrieved_at,
        cache_status="cached",
        licence="OGL-UK-3.0",
    )


def _indicators_result(value: float, source: SourceRef) -> GetIndicatorsOutput:
    return GetIndicatorsOutput(
        results=[
            IndicatorValue(
                place_id="ltla24:E08000032",
                indicator="education.fsm_eligibility_share",
                value=value,
                unit="proportion",
                period="2024/2025",
                source=source,
                confidence="official",
            )
        ],
        sources=[source],
        caveats=[],
        partial=False,
    )


def _state() -> Any:
    engine = MagicMock()
    return SimpleNamespace(geography_service=MagicMock(), orchestrator=MagicMock(), engine=engine)


async def test_dispatch_collects_sources_from_tool_results() -> None:
    dispatcher = ToolDispatcher(_state())
    older = NOW - timedelta(days=3)
    results = [
        _indicators_result(0.324, _source("dfe.explore", NOW)),
        _indicators_result(0.247, _source("dfe.explore", older)),
    ]
    with patch("soundings.ask.dispatcher.get_indicators", AsyncMock(side_effect=results)):
        await dispatcher.dispatch(
            "get_indicators", {"place_id": "ltla24:E08000032", "indicators": ["x"]}
        )
        await dispatcher.dispatch(
            "get_indicators", {"place_id": "ltla24:E92000001", "indicators": ["x"]}
        )

    # One entry per source, dated by the stalest retrieval used.
    assert [(s.source_id, s.retrieved_at) for s in dispatcher.sources] == [("dfe.explore", older)]


async def test_dispatch_records_tool_numbers_as_evidence() -> None:
    dispatcher = ToolDispatcher(_state())
    result = _indicators_result(0.324, _source("dfe.explore"))
    with patch("soundings.ask.dispatcher.get_indicators", AsyncMock(return_value=result)):
        await dispatcher.dispatch(
            "get_indicators", {"place_id": "ltla24:E08000032", "indicators": ["x"]}
        )

    assert dispatcher.evidence.supports(32.4, is_percent=True, decimals=1)


# --- the loop ---------------------------------------------------------------


class _Tool:
    def __init__(self, block_id: str, name: str, input_dict: dict[str, Any]) -> None:
        self.type, self.id, self.name, self.input = "tool_use", block_id, name, input_dict


class _Response:
    def __init__(self, content: list[Any]) -> None:
        self.content, self.stop_reason = content, "tool_use"


class _Client:
    def __init__(self, responses: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        queue = list(responses)

        def create(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return queue.pop(0)

        self.messages = SimpleNamespace(create=create)


def _compose(markdown: str, block_id: str) -> _Response:
    return _Response(
        [_Tool(block_id, "compose_answer", {"blocks": [{"type": "text", "markdown": markdown}]})]
    )


async def _run(responses: list[Any]) -> tuple[list[dict[str, Any]], _Client]:
    dispatcher = ToolDispatcher(_state())
    dispatcher._known_keys = AsyncMock(return_value={"education.fsm_eligibility_share"})  # type: ignore[method-assign]
    client = _Client(responses)
    events: list[dict[str, Any]] = []
    result = _indicators_result(0.324, _source("dfe.explore"))
    with (
        patch("soundings.ask.orchestrator.get_anthropic_client", return_value=client),
        patch("soundings.ask.dispatcher.get_indicators", AsyncMock(return_value=result)),
    ):
        orch = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=SystemPromptBuilder(),
            api_key="k",
            model="m",
        )
        await orch.run("How high is FSM eligibility in Bradford?", events.append)
    return events, client


GET = _Response(
    [_Tool("t1", "get_indicators", {"place_id": "ltla24:E08000032", "indicators": ["x"]})]
)


async def test_answer_emits_the_sources_its_tools_used() -> None:
    events, _ = await _run([GET, _compose("Bradford's FSM eligibility is 32.4%.", "t2")])

    (sources,) = [e for e in events if e["type"] == "sources"]
    assert [s["source_id"] for s in sources["sources"]] == ["dfe.explore"]
    (grounding,) = [e for e in events if e["type"] == "grounding"]
    assert grounding["untraced"] == []


async def test_untraced_figures_get_one_revision_before_streaming() -> None:
    events, client = await _run(
        [
            GET,
            _compose("Bradford is 32.4% against a regional 29.1%.", "t2"),
            _compose("Bradford's FSM eligibility is 32.4%.", "t3"),
        ]
    )

    # Nothing from the first draft reached the reader.
    texts = [e["block"]["markdown"] for e in events if e["type"] == "block"]
    assert texts == ["Bradford's FSM eligibility is 32.4%."]
    # The model was told exactly which figure failed and how to fix it.
    (feedback,) = [
        part
        for message in client.calls[2]["messages"]
        if message["role"] == "user" and isinstance(message["content"], list)
        for part in message["content"]
        if part.get("is_error")
    ]
    assert "29.1%" in feedback["content"]
    assert "calculate_" in feedback["content"]


async def test_still_untraced_after_revision_is_omitted() -> None:
    events, _ = await _run(
        [
            GET,
            _compose("Regional average 29.1%.", "t2"),
            _compose("Regional average still 29.1%.", "t3"),
        ]
    )

    texts = [e["block"]["markdown"] for e in events if e["type"] == "block"]
    assert len(texts) == 1
    assert "Regional average" not in texts[0]
    assert "omitted" in texts[0]
    (grounding,) = [e for e in events if e["type"] == "grounding"]
    assert grounding["untraced"] == ["29.1%"]
    assert grounding["revised"] is True


async def test_fallback_without_compose_answer_cannot_publish_invented_number() -> None:
    class _Text:
        type = "text"
        text = "The regional average is 29.1%."

    events, _ = await _run([GET, _Response([_Text()])])
    shown = [e["block"]["markdown"] for e in events if e["type"] == "block"]
    assert shown and all("29.1%" not in text for text in shown)
    assert any(e["type"] == "grounding" and e["untraced"] == ["29.1%"] for e in events)


async def test_unverified_figures_in_callout_evidence_are_removed() -> None:
    dispatcher = ToolDispatcher(_state())
    dispatcher._known_keys = AsyncMock(return_value=set())  # type: ignore[method-assign]
    finalised = await dispatcher.finalise_answer({
        "blocks": [{
            "type": "insight-callout", "severity": "notable",
            "headline": "Regional rate 29.1%", "evidence": "29.1% average",
        }]
    })
    assert finalised.untraced == ["29.1%"]


async def test_raw_html_is_escaped_from_text_answers() -> None:
    dispatcher = ToolDispatcher(_state())
    dispatcher._known_keys = AsyncMock(return_value=set())  # type: ignore[method-assign]
    finalised = await dispatcher.finalise_answer({
        "blocks": [{"type": "text", "markdown": "<script>alert(1)</script> **Bold**"}]
    })
    assert "<script>" not in finalised.answer.blocks[0].markdown
    assert "&lt;script&gt;" in finalised.answer.blocks[0].markdown
    assert "**Bold**" in finalised.answer.blocks[0].markdown
