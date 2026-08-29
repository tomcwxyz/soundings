"""HTTP routes for the three Phase 1 tools.

Mounted under `/v1/tools/...`. Each route validates input against the tool's
Pydantic model and returns the tool's output Pydantic. The same tool
implementations are also registered with the MCP server at `/mcp`.
"""

from fastapi import APIRouter, Request

from soundings.contracts.civil_society import CivilSocietyProfile
from soundings.contracts.observation import GetObservationsInput, GetObservationsOutput
from soundings.tools.compare_places import (
    ComparePlacesInput,
    ComparePlacesOutput,
    compare_places,
)
from soundings.tools.compare_places import tool_spec as compare_places_spec
from soundings.tools.find_organisations_in_place import (
    FindOrganisationsInPlaceInput,
    FindOrganisationsInPlaceOutput,
    find_organisations_in_place,
)
from soundings.tools.find_organisations_in_place import tool_spec as find_orgs_spec
from soundings.tools.find_place import (
    FindPlaceInput,
    FindPlaceOutput,
    find_place,
)
from soundings.tools.find_place import tool_spec as find_place_spec
from soundings.tools.get_civil_society_profile import (
    GetCivilSocietyProfileInput,
    get_civil_society_profile,
)
from soundings.tools.get_civil_society_profile import (
    tool_spec as get_civil_society_profile_spec,
)
from soundings.tools.get_indicators import (
    GetIndicatorsInput,
    GetIndicatorsOutput,
    get_indicators,
)
from soundings.tools.get_indicators import tool_spec as get_indicators_spec
from soundings.tools.get_observations import get_observations
from soundings.tools.get_observations import tool_spec as get_observations_spec
from soundings.tools.get_peer_distribution import (
    GetPeerDistributionInput,
    GetPeerDistributionOutput,
    get_peer_distribution,
)
from soundings.tools.get_peer_distribution import (
    tool_spec as get_peer_distribution_spec,
)
from soundings.tools.get_place_profile import (
    GetPlaceProfileInput,
    GetPlaceProfileOutput,
    get_place_profile,
)
from soundings.tools.get_place_profile import tool_spec as get_place_profile_spec
from soundings.tools.get_trend import (
    GetTrendInput,
    GetTrendOutput,
    get_trend,
)
from soundings.tools.get_trend import tool_spec as get_trend_spec

router = APIRouter(prefix="/v1/tools")


@router.get("")
async def list_tools() -> dict[str, list[dict[str, object]]]:
    return {
        "tools": [
            find_place_spec(),
            get_indicators_spec(),
            get_observations_spec(),
            get_place_profile_spec(),
            compare_places_spec(),
            get_trend_spec(),
            find_orgs_spec(),
            get_civil_society_profile_spec(),
            get_peer_distribution_spec(),
        ]
    }


@router.post("/find_place", response_model=FindPlaceOutput)
async def http_find_place(input: FindPlaceInput, request: Request) -> FindPlaceOutput:
    return await find_place(input, request.app.state.geography_service)


@router.post("/get_indicators", response_model=GetIndicatorsOutput)
async def http_get_indicators(input: GetIndicatorsInput, request: Request) -> GetIndicatorsOutput:
    return await get_indicators(input, request.app.state.orchestrator)


@router.post("/get_observations", response_model=GetObservationsOutput)
async def http_get_observations(
    input: GetObservationsInput, request: Request
) -> GetObservationsOutput:
    return await get_observations(input, request.app.state.engine)


@router.post("/get_place_profile", response_model=GetPlaceProfileOutput)
async def http_get_place_profile(
    input: GetPlaceProfileInput, request: Request
) -> GetPlaceProfileOutput:
    return await get_place_profile(
        input,
        request.app.state.orchestrator,
        request.app.state.engine,
    )


@router.post("/compare_places", response_model=ComparePlacesOutput)
async def http_compare_places(input: ComparePlacesInput, request: Request) -> ComparePlacesOutput:
    return await compare_places(input, request.app.state.orchestrator)


@router.post("/get_trend", response_model=GetTrendOutput)
async def http_get_trend(input: GetTrendInput, request: Request) -> GetTrendOutput:
    return await get_trend(input, request.app.state.orchestrator)


@router.post("/find_organisations_in_place", response_model=FindOrganisationsInPlaceOutput)
async def http_find_organisations(
    input: FindOrganisationsInPlaceInput, request: Request
) -> FindOrganisationsInPlaceOutput:
    return await find_organisations_in_place(input, request.app.state.orchestrator)


@router.post("/get_civil_society_profile", response_model=CivilSocietyProfile)
async def http_get_civil_society_profile(
    input: GetCivilSocietyProfileInput,
    request: Request,
) -> CivilSocietyProfile:
    return await get_civil_society_profile(input, request.app.state.orchestrator)


@router.post("/get_peer_distribution", response_model=GetPeerDistributionOutput)
async def http_get_peer_distribution(
    input: GetPeerDistributionInput, request: Request
) -> GetPeerDistributionOutput:
    return await get_peer_distribution(input, request.app.state.orchestrator)
