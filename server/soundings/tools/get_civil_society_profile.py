"""get_civil_society_profile tool — aggregate civil society profile for a place.

Returns total active charities, income distribution + median/mean,
and a registration-cohort series. Backed by `data.organisation` +
`data.organisation_operates_in` populated by the CC bulk loader.
"""

from typing import Any

from pydantic import BaseModel, Field

from soundings.contracts.civil_society import CivilSocietyProfile


class GetCivilSocietyProfileInput(BaseModel):
    place_id: str = Field(
        description=(
            "Canonical geography place ID (e.g. ltla24:E06000047). The"
            " profile is computed from charities whose registered address"
            " resolves to this place via `data.organisation_operates_in`."
        )
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Optional cause keywords to focus the profile on a theme. When set,"
            " every count and the income distribution cover only charities whose"
            " name or charitable objects match one of these terms"
            " (case-insensitive substring). Supply several near-synonyms for"
            " recall — e.g. for food poverty: ['food bank', 'food poverty',"
            " 'hunger', 'foodbank', 'poverty']. Leave empty for the whole sector."
        ),
    )
    year_from: int | None = Field(
        default=None,
        description=(
            "Optional lower bound for the registration cohort series"
            " (e.g. 2015 for 'since 2015'). When set, only cohort years >="
            " year_from are returned. Does not affect totals or income."
        ),
    )
    year_to: int | None = Field(
        default=None,
        description=(
            "Optional upper bound for the registration cohort series."
            " When set, only cohort years <= year_to are returned."
        ),
    )


TOOL_NAME = "get_civil_society_profile"
TOOL_DESCRIPTION = (
    "Aggregate civil society profile for a UK place — total registered "
    "charities, annual-income distribution (with median + mean), and a "
    "year-by-year registration cohort series. England/Wales use declared "
    "CC operating areas. Scotland uses a partial OSCR registered-address "
    "postcode count with an explicit coverage_basis and warnings."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetCivilSocietyProfileInput.model_json_schema(),
        "output_schema": CivilSocietyProfile.model_json_schema(),
    }


async def get_civil_society_profile(
    input: GetCivilSocietyProfileInput,
    orchestrator: Any,
) -> CivilSocietyProfile:
    """Tool handler — delegates to the orchestrator method."""
    result: CivilSocietyProfile = await orchestrator.compute_civil_society_profile(
        place_id=input.place_id,
        keywords=input.keywords,
        year_from=input.year_from,
        year_to=input.year_to,
    )
    return result
