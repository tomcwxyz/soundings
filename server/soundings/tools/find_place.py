"""find_place tool — resolve a place reference (postcode or name) to canonical
geography IDs.

Per spec §4.1. Postcode inputs route through GeographyService and return all
containing levels with confidence 1.0. Name inputs go through pg_trgm fuzzy
search with similarity score as confidence, ranked by hierarchy depth on ties.
"""

import re
from datetime import date

from pydantic import BaseModel, Field

from soundings.geography.service import GeographyService

UK_POSTCODE_RE = re.compile(r"^\s*[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\s*$", re.IGNORECASE)

# Used to break similarity-score ties: deepest level wins so a name match for
# "Newcastle" prefers the LTLA over the region.
DEPTH_BY_TYPE = {
    "country": 0,
    "region": 1,
    "utla24": 2,
    "ltla24": 3,
    "westminster_constituency_24": 3,
    "msoa21": 4,
    "ward24": 4,
    "lsoa21": 5,
}


class FindPlaceInput(BaseModel):
    query: str
    geography_types: list[str] | None = None
    limit: int = 10
    as_of: date | None = Field(
        default=None,
        description=(
            "For name queries, resolve places valid on this date. Postcode queries remain "
            "current-vintage only."
        ),
    )


class PlaceMatch(BaseModel):
    id: str
    name: str
    type: str
    parent_ids: list[str] = Field(default_factory=list)
    confidence: float


class FindPlaceOutput(BaseModel):
    matches: list[PlaceMatch] = Field(default_factory=list)


TOOL_NAME = "find_place"
TOOL_DESCRIPTION = (
    "Resolve a place reference (UK postcode or natural-language name) to one or more canonical "
    "Soundings geography IDs with confidence scores. Name queries can optionally resolve places "
    "valid on a historical as_of date; postcode resolution is current-vintage only."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": FindPlaceInput.model_json_schema(),
        "output_schema": FindPlaceOutput.model_json_schema(),
    }


async def find_place(input: FindPlaceInput, service: GeographyService) -> FindPlaceOutput:
    if UK_POSTCODE_RE.match(input.query):
        return await _find_by_postcode(input, service)
    return await _find_by_name(input, service)


async def _find_by_postcode(input: FindPlaceInput, service: GeographyService) -> FindPlaceOutput:
    result = await service.find_place_by_postcode(input.query)
    if result is None:
        return FindPlaceOutput()
    types_filter = set(input.geography_types or [])
    matches: list[PlaceMatch] = [
        PlaceMatch(
            id=place.id,
            name=place.name,
            type=place.type,
            parent_ids=[],
            confidence=1.0,
        )
        for place_type, place in result.items()
        if not types_filter or place_type in types_filter
    ]
    matches.sort(key=lambda m: DEPTH_BY_TYPE.get(m.type, 99), reverse=True)
    return FindPlaceOutput(matches=matches[: input.limit])


async def _find_by_name(input: FindPlaceInput, service: GeographyService) -> FindPlaceOutput:
    raw = await service.find_place_by_name(
        input.query,
        geography_types=input.geography_types,
        limit=input.limit,
        as_of=input.as_of,
    )
    enriched = sorted(
        raw,
        key=lambda m: (-m.confidence, -DEPTH_BY_TYPE.get(m.place.type, 99)),
    )
    return FindPlaceOutput(
        matches=[
            PlaceMatch(
                id=m.place.id,
                name=m.place.name,
                type=m.place.type,
                parent_ids=[],
                confidence=m.confidence,
            )
            for m in enriched
        ]
    )
