from datetime import date
from types import SimpleNamespace

from soundings.ask.dispatcher import ToolDispatcher
from soundings.geography.service import ContainmentResult


class _FakeGeographyService:
    async def find_containing_places_context(
        self,
        place_id: str,
        *,
        boundary_mode: str = "current_boundary",
        as_of: date | None = None,
    ) -> ContainmentResult:
        assert place_id == "gss_e01:TESTCHILD"
        assert boundary_mode == "historical"
        assert as_of == date(2015, 6, 1)
        return ContainmentResult(
            places=(
                SimpleNamespace(
                    id="gss_e07:TESTPARENT",
                    name="Historic district",
                    type="gss_e07",
                    code="TESTPARENT",
                ),
            ),
            boundary_mode="historical",
            boundary_date=date(2015, 6, 1),
            as_of=date(2015, 6, 1),
        )


def _dispatcher() -> ToolDispatcher:
    return ToolDispatcher(SimpleNamespace(geography_service=_FakeGeographyService()))


def test_dispatcher_advertises_containing_places() -> None:
    names = {spec["name"] for spec in _dispatcher().tool_specs()}
    assert "get_containing_places" in names


async def test_dispatcher_routes_historical_containment() -> None:
    result = await _dispatcher().dispatch(
        "get_containing_places",
        {
            "place_id": "gss_e01:TESTCHILD",
            "boundary_mode": "historical",
            "as_of": "2015-06-01",
        },
    )

    assert result["boundary_mode"] == "historical"
    assert result["boundary_date"] == "2015-06-01"
    assert result["places"][0]["id"] == "gss_e07:TESTPARENT"
