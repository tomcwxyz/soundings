"""Contract tests for the validity-aware place containment tool."""

from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from soundings.geography.service import ContainmentResult
from soundings.tools.get_containing_places import (
    GetContainingPlacesInput,
    GetContainingPlacesOutput,
    get_containing_places,
    tool_spec,
)


def test_input_defaults_to_current_boundary() -> None:
    input = GetContainingPlacesInput(place_id="lsoa21:E01000001")
    assert input.boundary_mode == "current_boundary"
    assert input.as_of is None


def test_historical_mode_requires_as_of() -> None:
    with pytest.raises(ValidationError, match="as_of"):
        GetContainingPlacesInput(
            place_id="gss_e01:E01000001",
            boundary_mode="historical",
        )


def test_output_round_trips() -> None:
    output = GetContainingPlacesOutput(
        place_id="gss_e01:E01000001",
        boundary_mode="historical",
        boundary_date=date(2015, 6, 1),
        as_of=date(2015, 6, 1),
        places=[
            {
                "id": "gss_e07:E07000001",
                "name": "Old District",
                "type": "gss_e07",
                "code": "E07000001",
            }
        ],
        partial=True,
        caveats=["Some historical evidence is incomplete."],
    )
    restored = GetContainingPlacesOutput.model_validate(output.model_dump())
    assert restored == output


def test_tool_spec_advertises_temporal_semantics() -> None:
    spec = tool_spec()
    assert spec["name"] == "get_containing_places"
    assert "historical" in str(spec["description"]).lower()
    assert "input_schema" in spec
    assert "output_schema" in spec


class _FakeGeographyService:
    def __init__(self) -> None:
        self.call: dict[str, object] | None = None

    async def find_containing_places_context(
        self,
        place_id: str,
        *,
        boundary_mode: str = "current_boundary",
        as_of: date | None = None,
    ) -> ContainmentResult:
        self.call = {
            "place_id": place_id,
            "boundary_mode": boundary_mode,
            "as_of": as_of,
        }
        if boundary_mode == "current_boundary":
            return ContainmentResult(
                places=(),
                boundary_mode="current_boundary",
                boundary_date=date.today(),
                as_of=as_of,
            )
        return ContainmentResult(
            places=(
                SimpleNamespace(
                    id="gss_e07:E07000001",
                    name="Old District",
                    type="gss_e07",
                    code="E07000001",
                ),
            ),
            boundary_mode="historical",
            boundary_date=date(2015, 6, 1),
            as_of=date(2015, 6, 1),
            partial=True,
            caveats=("Some historical evidence is incomplete.",),
        )


async def test_tool_routes_current_context_by_default() -> None:
    service = _FakeGeographyService()
    result = await get_containing_places(
        GetContainingPlacesInput(place_id="lsoa21:E01000001"),
        service,  # type: ignore[arg-type]
    )

    assert service.call == {
        "place_id": "lsoa21:E01000001",
        "boundary_mode": "current_boundary",
        "as_of": None,
    }
    assert result.boundary_mode == "current_boundary"
    assert result.as_of is None
    assert result.partial is False


async def test_tool_routes_historical_context_and_preserves_caveats() -> None:
    service = _FakeGeographyService()
    input = GetContainingPlacesInput(
        place_id="gss_e01:E01000001",
        boundary_mode="historical",
        as_of=date(2015, 6, 1),
    )

    result = await get_containing_places(input, service)  # type: ignore[arg-type]

    assert service.call == {
        "place_id": "gss_e01:E01000001",
        "boundary_mode": "historical",
        "as_of": date(2015, 6, 1),
    }
    assert result.places[0].id == "gss_e07:E07000001"
    assert result.partial is True
    assert result.caveats == ["Some historical evidence is incomplete."]
