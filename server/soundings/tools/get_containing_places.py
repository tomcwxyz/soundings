"""get_containing_places tool — resolve current or historical place containment."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from soundings.geography.service import GeographyService

BoundaryMode = Literal["current_boundary", "historical"]


class GetContainingPlacesInput(BaseModel):
    place_id: str
    boundary_mode: BoundaryMode = "current_boundary"
    as_of: date | None = Field(
        default=None,
        description=(
            "Date used for historical containment. Required when boundary_mode is 'historical'. "
            "In current_boundary mode it is retained only as observation context."
        ),
    )

    @model_validator(mode="after")
    def validate_historical_date(self) -> "GetContainingPlacesInput":
        if self.boundary_mode == "historical" and self.as_of is None:
            raise ValueError("as_of is required when boundary_mode is 'historical'")
        return self


class ContainingPlace(BaseModel):
    id: str
    name: str
    type: str
    code: str


class GetContainingPlacesOutput(BaseModel):
    place_id: str
    places: list[ContainingPlace] = Field(default_factory=list)
    boundary_mode: BoundaryMode
    boundary_date: date
    as_of: date | None = None
    partial: bool = False
    caveats: list[str] = Field(default_factory=list)


TOOL_NAME = "get_containing_places"
TOOL_DESCRIPTION = (
    "Get the places that contain a canonical Soundings place using either today's boundaries or "
    "dated historical boundaries. Use historical mode with as_of for questions such as 'which "
    "local authority contained this area in 2015?'. Historical mode uses dated hierarchy evidence "
    "only and does not silently substitute current boundaries."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetContainingPlacesInput.model_json_schema(),
        "output_schema": GetContainingPlacesOutput.model_json_schema(),
    }


async def get_containing_places(
    input: GetContainingPlacesInput,
    service: GeographyService,
) -> GetContainingPlacesOutput:
    result = await service.find_containing_places_context(
        input.place_id,
        boundary_mode=input.boundary_mode,
        as_of=input.as_of,
    )
    return GetContainingPlacesOutput(
        place_id=input.place_id,
        places=[
            ContainingPlace(
                id=place.id,
                name=place.name,
                type=place.type,
                code=place.code,
            )
            for place in result.places
        ],
        boundary_mode=result.boundary_mode,
        boundary_date=result.boundary_date,
        as_of=result.as_of,
        partial=result.partial,
        caveats=list(result.caveats),
    )
