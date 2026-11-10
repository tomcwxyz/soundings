"""Unit tests for the get_peer_distribution tool."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from soundings.catalogue.contracts import ContractRegistry
from soundings.contracts.source_ref import SourceRef
from soundings.tools.get_peer_distribution import (
    TOOL_NAME,
    GetPeerDistributionInput,
    GetPeerDistributionOutput,
    get_peer_distribution,
    tool_spec,
)

# --- tool_spec ------------------------------------------------------------


def test_tool_spec_has_correct_name() -> None:
    spec = tool_spec()
    assert spec["name"] == TOOL_NAME
    assert spec["name"] == "get_peer_distribution"


def test_tool_spec_has_required_keys() -> None:
    spec = tool_spec()
    assert "name" in spec
    assert "description" in spec
    assert "input_schema" in spec
    assert "output_schema" in spec


def test_tool_spec_description_mentions_distribution() -> None:
    spec = tool_spec()
    description: str = spec["description"]  # type: ignore[assignment]
    assert "distribution" in description.lower()


# --- input model ----------------------------------------------------------


def test_input_model_validates_required_fields() -> None:
    model = GetPeerDistributionInput(
        indicator_key="population.total",
        place_id="ltla24:E06000047",
    )
    assert model.indicator_key == "population.total"
    assert model.place_id == "ltla24:E06000047"
    assert model.period is None


def test_input_model_accepts_period() -> None:
    model = GetPeerDistributionInput(
        indicator_key="population.total",
        place_id="ltla24:E06000047",
        period="2024",
    )
    assert model.period == "2024"


def test_input_model_rejects_missing_indicator_key() -> None:
    with pytest.raises(ValidationError):
        GetPeerDistributionInput(place_id="ltla24:E06000047")  # type: ignore[call-arg]


def test_input_model_rejects_missing_place_id() -> None:
    with pytest.raises(ValidationError):
        GetPeerDistributionInput(indicator_key="population.total")  # type: ignore[call-arg]


# --- output model ---------------------------------------------------------


def test_output_model_validates_with_peer_place_values() -> None:
    out = GetPeerDistributionOutput(
        indicator_key="population.total",
        place_id="ltla24:E06000047",
        focal_value=200000.0,
        peer_values=[100000.0, 200000.0, 300000.0],
        peer_place_values=[
            {"place_id": "ltla24:E06000047", "value": 200000.0},
            {"place_id": "ltla24:E06000048", "value": 100000.0},
        ],
        peer_count=3,
        unit="people",
        period="2024",
        sources=[],
        caveats=[],
    )
    assert out.indicator_key == "population.total"
    assert out.focal_value == 200000.0
    assert len(out.peer_values) == 3
    assert len(out.peer_place_values) == 2
    assert out.peer_place_values[0]["place_id"] == "ltla24:E06000047"
    assert out.peer_count == 3


def test_output_model_peer_place_values_defaults_empty() -> None:
    out = GetPeerDistributionOutput(
        indicator_key="population.total",
        place_id="ltla24:E06000047",
        focal_value=None,
        peer_values=[],
        peer_place_values=[],
        peer_count=0,
        unit="people",
        period="2024",
        sources=[],
        caveats=[],
    )
    assert out.peer_place_values == []


# --- async function with mocked orchestrator -----------------------------


class _FakeAdapter:
    def get_source_ref(self, *, retrieved_at: datetime, cache_status: str) -> SourceRef:
        return SourceRef(
            source_id="fake.source",
            source_label="Fake",
            publisher="Test",
            retrieved_at=retrieved_at,
            cache_status="cached",
            licence="CC0",
        )


class _FakeRegistry:
    async def adapter_for_indicator(self, indicator_key: str) -> _FakeAdapter:
        return _FakeAdapter()


class _FakeOrchestrator:
    def __init__(
        self,
        peer_values: dict[str, float | None],
        period_used: str,
        meta: dict[str, str] | None,
    ) -> None:
        self._peer_values = peer_values
        self._period_used = period_used
        self._meta = meta
        self.enforce_level_called = False
        self.peer_values_loader_called = False
        self.load_indicator_meta_called = False
        # No contracts: these tests are about plumbing, not value checks.
        self._contracts = ContractRegistry([])
        self._registry = _FakeRegistry()

    async def _enforce_level(self, indicator_key: str, place_id: str) -> None:
        self.enforce_level_called = True
        self.enforce_level_args = (indicator_key, place_id)

    async def _peer_values_loader(
        self, *, indicator_key: str, peer_type: str, period: str | None
    ) -> tuple[dict[str, float | None], str]:
        self.peer_values_loader_called = True
        self.peer_values_loader_args = {
            "indicator_key": indicator_key,
            "peer_type": peer_type,
            "period": period,
        }
        return self._peer_values, self._period_used

    async def _load_indicator_meta(self, indicator_key: str) -> dict[str, str] | None:
        self.load_indicator_meta_called = True
        self.load_indicator_meta_args = (indicator_key,)
        return self._meta


@pytest.mark.asyncio
async def test_get_peer_distribution_calls_orchestrator_methods() -> None:
    orchestrator = _FakeOrchestrator(
        peer_values={
            "ltla24:E06000047": 200000.0,
            "ltla24:E06000048": 100000.0,
            "ltla24:E06000049": None,
        },
        period_used="2024",
        meta={"unit": "people"},
    )
    inp = GetPeerDistributionInput(
        indicator_key="population.total",
        place_id="ltla24:E06000047",
        period="2024",
    )
    result = await get_peer_distribution(inp, orchestrator)  # type: ignore[arg-type]

    assert orchestrator.enforce_level_called
    assert orchestrator.peer_values_loader_called
    assert orchestrator.load_indicator_meta_called
    # peer_type should be derived from place_id partition
    assert orchestrator.peer_values_loader_args["peer_type"] == "ltla24"
    assert orchestrator.peer_values_loader_args["period"] == "2024"
    assert isinstance(result, GetPeerDistributionOutput)
    assert result.focal_value == 200000.0
    # non-null values only in peer_values
    assert 100000.0 in result.peer_values
    assert None not in result.peer_values  # type: ignore[comparison-overlap]
    assert result.unit == "people"
    assert result.period == "2024"


@pytest.mark.asyncio
async def test_get_peer_distribution_builds_peer_place_values() -> None:
    orchestrator = _FakeOrchestrator(
        peer_values={
            "ltla24:E06000047": 200000.0,
            "ltla24:E06000048": 100000.0,
        },
        period_used="2024",
        meta={"unit": "people"},
    )
    inp = GetPeerDistributionInput(
        indicator_key="population.total",
        place_id="ltla24:E06000047",
    )
    result = await get_peer_distribution(inp, orchestrator)  # type: ignore[arg-type]

    place_ids_in_ppv = [d["place_id"] for d in result.peer_place_values]
    assert "ltla24:E06000047" in place_ids_in_ppv
    assert "ltla24:E06000048" in place_ids_in_ppv
    # Each entry is a dict with place_id and value keys
    for entry in result.peer_place_values:
        assert "place_id" in entry
        assert "value" in entry


@pytest.mark.asyncio
async def test_get_peer_distribution_handles_missing_focal_value() -> None:
    orchestrator = _FakeOrchestrator(
        peer_values={
            "ltla24:E06000048": 100000.0,
        },
        period_used="2024",
        meta={"unit": "people"},
    )
    inp = GetPeerDistributionInput(
        indicator_key="population.total",
        place_id="ltla24:E06000047",
    )
    result = await get_peer_distribution(inp, orchestrator)  # type: ignore[arg-type]

    assert result.focal_value is None
    assert result.peer_count == 1


@pytest.mark.asyncio
async def test_get_peer_distribution_defaults_unit_when_no_meta() -> None:
    orchestrator = _FakeOrchestrator(
        peer_values={"ltla24:E06000047": 200000.0},
        period_used="2024",
        meta=None,
    )
    inp = GetPeerDistributionInput(
        indicator_key="population.total",
        place_id="ltla24:E06000047",
    )
    result = await get_peer_distribution(inp, orchestrator)  # type: ignore[arg-type]

    assert result.unit == "value"
