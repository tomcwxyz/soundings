"""Summarise a funder from the local 360Giving grant index."""

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.threesixtygiving.client import ThreeSixtyGivingClient
from soundings.grants.status import get_grant_index_status
from soundings.grants.store import GrantStore

ORG_ID_PREFIXES = ("GB-CHC-", "GB-COH-", "GB-GOR-", "GB-SC-", "GB-NIC-", "360G-")


class RankedFundingItem(BaseModel):
    name: str | None = None
    id: str | None = None
    programme: str | None = None
    grants: int
    total_gbp: float


class GetFunderProfileInput(BaseModel):
    funder: str = Field(description="360Giving funder Org ID or funder name.")
    top_n: int = Field(default=10, ge=1, le=25)
    refresh: bool = Field(
        default=True,
        description=(
            "When funder is an exact 360Giving Org ID, refresh that funder's "
            "grants from the official API before profiling. Ignored for "
            "name-only lookups."
        ),
    )


class GetFunderProfileOutput(BaseModel):
    funder_id: str | None = None
    funder_name: str | None = None
    grants: int
    total_gbp: float
    average_gbp: float | None = None
    earliest_award: str | None = None
    latest_award: str | None = None
    top_recipients: list[RankedFundingItem] = Field(default_factory=list)
    top_programmes: list[RankedFundingItem] = Field(default_factory=list)
    refreshed_from_api: bool = False
    index_complete: bool
    index_coverage: str
    caveats: list[str] = Field(default_factory=list)


TOOL_NAME = "get_funder_profile"
TOOL_DESCRIPTION = (
    "Profile a funder using 360Giving grants: grant count, GBP total/average, "
    "award date range, top recipients and programmes. Exact 360Giving Org IDs "
    "can be refreshed from the official API before analysis; name-only lookups "
    "use the local index."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetFunderProfileInput.model_json_schema(),
        "output_schema": GetFunderProfileOutput.model_json_schema(),
    }


def _looks_like_org_id(value: str) -> bool:
    return value.startswith(ORG_ID_PREFIXES) and " " not in value


async def get_funder_profile(
    input: GetFunderProfileInput,
    engine: AsyncEngine,
) -> GetFunderProfileOutput:
    store = GrantStore(engine)
    refreshed = False
    caveats: list[str] = []

    if input.refresh and _looks_like_org_id(input.funder):
        client = ThreeSixtyGivingClient()
        raw = [grant async for grant in client.iter_grants_made(input.funder)]
        await store.upsert_api_grants(raw)
        refreshed = True
    elif input.refresh:
        caveats.append(
            "Funder was supplied as a name rather than a 360Giving Org ID, so "
            "no targeted API refresh was attempted."
        )

    profile = await store.funder_profile(input.funder, top_n=input.top_n)
    status = await get_grant_index_status(engine)
    if not status["complete"] and not refreshed:
        caveats.append(
            "Profile is based on the currently indexed subset of 360Giving "
            "data, not yet the full corpus."
        )
    elif not status["complete"] and refreshed:
        caveats.append(
            "This funder's API records were refreshed, but the wider local "
            "grant index is not yet full-corpus."
        )

    recipients = [
        RankedFundingItem(
            name=row.get("name"),
            id=row.get("id"),
            grants=int(row.get("grants") or 0),
            total_gbp=float(row.get("total_gbp") or 0),
        )
        for row in profile["top_recipients"]
    ]
    programmes = [
        RankedFundingItem(
            name=row.get("programme"),
            programme=row.get("programme"),
            grants=int(row.get("grants") or 0),
            total_gbp=float(row.get("total_gbp") or 0),
        )
        for row in profile["top_programmes"]
    ]
    return GetFunderProfileOutput(
        funder_id=profile["funder_id"],
        funder_name=profile["funder_name"],
        grants=profile["grants"],
        total_gbp=float(profile["total_gbp"] or 0),
        average_gbp=profile["average_gbp"],
        earliest_award=profile["earliest_award"],
        latest_award=profile["latest_award"],
        top_recipients=recipients,
        top_programmes=programmes,
        refreshed_from_api=refreshed,
        index_complete=bool(status["complete"]),
        index_coverage=str(status["coverage"]),
        caveats=caveats,
    )
