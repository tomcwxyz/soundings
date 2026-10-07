"""Search the local 360Giving grant index."""

from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.grants.status import get_grant_index_status
from soundings.grants.store import GrantStore


class GrantSearchResult(BaseModel):
    id: str
    title: str | None = None
    funder_id: str | None = None
    funder_name: str | None = None
    recipient_external_id: str | None = None
    recipient_name: str | None = None
    amount: float | None = None
    currency: str | None = None
    awarded_on: str | None = None
    purpose: str | None = None
    programme: str | None = None
    beneficiary_place_ids: list[str] = Field(default_factory=list)
    relevance: float = 0.0


class GrantFunderSummary(BaseModel):
    funder_id: str | None = None
    funder_name: str
    grants: int
    total_gbp: float


class SearchGrantsInput(BaseModel):
    query: str | None = Field(
        default=None,
        description=(
            "Optional natural-language/keyword search across grant title, "
            "purpose, programme, funder and recipient. Uses PostgreSQL "
            "web-style full-text search."
        ),
    )
    funder: str | None = Field(
        default=None,
        description="Optional 360Giving funder Org ID or part of a funder name.",
    )
    recipient: str | None = Field(
        default=None,
        description=(
            "Optional recipient Org ID, Soundings organisation ID, or part of a recipient name."
        ),
    )
    place_id: str | None = Field(
        default=None,
        description=(
            "Optional canonical Soundings place ID at any supported level, including "
            "local authority, region or country. Matches indexed beneficiary places "
            "within that geography, or recipients known to operate there when a grant "
            "has no beneficiary geography."
        ),
    )
    awarded_from: date | None = None
    awarded_to: date | None = None
    amount_min: float | None = Field(default=None, ge=0)
    amount_max: float | None = Field(default=None, ge=0)
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    funder_limit: int = Field(
        default=10,
        ge=1,
        le=25,
        description="Number of ranked funder aggregates to return across all matching grants.",
    )


class SearchGrantsOutput(BaseModel):
    grants: list[GrantSearchResult] = Field(default_factory=list)
    total: int
    total_gbp: float
    top_funders: list[GrantFunderSummary] = Field(default_factory=list)
    limit: int
    offset: int
    index_complete: bool
    index_coverage: str
    caveats: list[str] = Field(default_factory=list)


TOOL_NAME = "search_grants"
TOOL_DESCRIPTION = (
    "Search indexed 360Giving grants by topic, funder, recipient, geography, date "
    "or amount. Geography can be local, regional or national. Returns matching "
    "grant evidence plus deterministic total value and ranked funder aggregates "
    "across the full matched set. Check index_complete before treating the results "
    "as full-corpus coverage."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": SearchGrantsInput.model_json_schema(),
        "output_schema": SearchGrantsOutput.model_json_schema(),
    }


async def search_grants(input: SearchGrantsInput, engine: AsyncEngine) -> SearchGrantsOutput:
    store = GrantStore(engine)
    result = await store.search(
        query=input.query,
        funder=input.funder,
        recipient=input.recipient,
        place_id=input.place_id,
        awarded_from=input.awarded_from,
        awarded_to=input.awarded_to,
        amount_min=input.amount_min,
        amount_max=input.amount_max,
        limit=input.limit,
        offset=input.offset,
        funder_limit=input.funder_limit,
    )
    status = await get_grant_index_status(engine)
    caveats: list[str] = []
    if not status["complete"]:
        caveats.append(
            "360Giving index coverage is currently partial (write-through from "
            "targeted API calls); absence from these results must not be "
            "interpreted as absence from the full 360Giving corpus."
        )
    return SearchGrantsOutput(
        grants=[GrantSearchResult.model_validate(row) for row in result["grants"]],
        total=result["total"],
        total_gbp=result["total_gbp"],
        top_funders=[
            GrantFunderSummary.model_validate(row) for row in result["top_funders"]
        ],
        limit=result["limit"],
        offset=result["offset"],
        index_complete=bool(status["complete"]),
        index_coverage=str(status["coverage"]),
        caveats=caveats,
    )
