"""get_civil_society_profile tool — aggregate civil society profile for a place.

Current totals, income and organisation detail remain backed by the active
Charity Commission organisation surface. The year-by-year registration/removal
cohort is replaced from `data.organisation_lifecycle`, so removed charities are
not silently absent from historical change.
"""

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from soundings.contracts.civil_society import CivilSocietyProfile, RegistrationCohort


class GetCivilSocietyProfileInput(BaseModel):
    place_id: str = Field(
        description=(
            "Canonical geography place ID (e.g. ltla24:E06000047). The"
            " current profile is computed from charities associated with this"
            " place; lifecycle cohorts use registered-address geography."
        )
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Optional cause keywords to focus the current profile on a theme."
            " Historical lifecycle cohorts are omitted for filtered profiles"
            " because lifecycle facts do not retain historical classifications."
        ),
    )
    year_from: int | None = Field(
        default=None,
        description=(
            "Optional lower bound for the lifecycle cohort series"
            " (e.g. 2015 for 'since 2015'). When set, only cohort years >="
            " year_from are returned. Does not affect totals or income."
        ),
    )
    year_to: int | None = Field(
        default=None,
        description=(
            "Optional upper bound for the lifecycle cohort series."
            " When set, only cohort years <= year_to are returned."
        ),
    )


TOOL_NAME = "get_civil_society_profile"
TOOL_DESCRIPTION = (
    "Aggregate civil society profile for a UK place — current registered "
    "charities, annual-income distribution, and Charity Commission registration/"
    "register-removal cohorts. Coverage: England + Wales."
)

LIFECYCLE_CAVEAT = (
    "Registration/removal cohorts use the Charity Commission's registration and "
    "register-removal dates, attributed by current or last-known registered-address "
    "postcode. Register removal is not necessarily the date an organisation ceased "
    "operating, and the address may differ from the address held at the event date."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetCivilSocietyProfileInput.model_json_schema(),
        "output_schema": CivilSocietyProfile.model_json_schema(),
    }


async def _lifecycle_cohort(
    engine: Any,
    place_id: str,
    *,
    year_from: int | None,
    year_to: int | None,
) -> list[RegistrationCohort]:
    clauses = ["registered_address_place_id = :pid"]
    params: dict[str, Any] = {"pid": place_id}
    if year_from is not None:
        clauses.append("event_year >= :year_from")
        params["year_from"] = year_from
    if year_to is not None:
        clauses.append("event_year <= :year_to")
        params["year_to"] = year_to

    rows = []
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "WITH events AS ("
                    "  SELECT registered_address_place_id, "
                    "         EXTRACT(YEAR FROM registered_on)::int AS event_year, "
                    "         1 AS registered, 0 AS removed "
                    "  FROM data.organisation_lifecycle "
                    "  WHERE registered_on IS NOT NULL "
                    "  UNION ALL "
                    "  SELECT registered_address_place_id, "
                    "         EXTRACT(YEAR FROM removed_on)::int AS event_year, "
                    "         0 AS registered, 1 AS removed "
                    "  FROM data.organisation_lifecycle "
                    "  WHERE removed_on IS NOT NULL"
                    ") "
                    "SELECT event_year AS year, SUM(registered) AS registered, "
                    "       SUM(removed) AS removed "
                    "FROM events WHERE "
                    + " AND ".join(clauses)
                    + " GROUP BY event_year ORDER BY event_year"
                ),
                params,
            )
        ).all()

    return [
        RegistrationCohort(
            year=int(row.year),
            registered=int(row.registered),
            removed=int(row.removed),
            net=int(row.registered) - int(row.removed),
        )
        for row in rows
    ]


async def get_civil_society_profile(
    input: GetCivilSocietyProfileInput,
    orchestrator: Any,
) -> CivilSocietyProfile:
    """Tool handler — current profile plus truthful lifecycle cohorts."""
    result: CivilSocietyProfile = await orchestrator.compute_civil_society_profile(
        place_id=input.place_id,
        keywords=input.keywords,
        year_from=input.year_from,
        year_to=input.year_to,
    )

    caveats = list(result.caveats)
    if input.keywords:
        caveats.append(
            "Lifecycle cohorts are omitted for keyword-filtered profiles because "
            "the compact lifecycle facts do not retain historical cause classifications."
        )
        return result.model_copy(
            update={
                "registration_cohort": [],
                "caveats": caveats,
                "partial": True,
            }
        )

    cohort = await _lifecycle_cohort(
        orchestrator._engine,
        input.place_id,
        year_from=input.year_from,
        year_to=input.year_to,
    )
    caveats.append(LIFECYCLE_CAVEAT)
    return result.model_copy(
        update={
            "registration_cohort": cohort,
            "caveats": caveats,
        }
    )
