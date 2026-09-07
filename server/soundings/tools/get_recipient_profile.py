"""Summarise a grant recipient from the local 360Giving corpus."""

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.threesixtygiving.client import ThreeSixtyGivingClient
from soundings.grants.analytics import GrantAnalytics
from soundings.grants.status import get_grant_index_status
from soundings.grants.store import GrantStore

ORG_ID_PREFIXES = ("GB-CHC-", "GB-COH-", "GB-GOR-", "GB-SC-", "GB-NIC-", "360G-")


class RankedRecipientFunder(BaseModel):
    name: str | None = None
    id: str | None = None
    grants: int
    total_gbp: float


class RecipientProgramme(BaseModel):
    programme: str
    grants: int
    total_gbp: float


class RecipientGrantYear(BaseModel):
    year: int
    grants: int
    total_gbp: float


class RecipientGrantEvidence(BaseModel):
    id: str
    title: str | None = None
    funder_id: str | None = None
    funder_name: str | None = None
    amount: float | None = None
    currency: str | None = None
    awarded_on: str | None = None
    purpose: str | None = None
    programme: str | None = None
    beneficiary_place_ids: list[str] = Field(default_factory=list)


class RecipientAlternativeMatch(BaseModel):
    recipient_id: str | None = None
    recipient_name: str | None = None
    grants: int
    total_gbp: float


class GetRecipientProfileInput(BaseModel):
    recipient: str = Field(
        description=(
            "360Giving recipient Org ID, Soundings organisation ID "
            "(for example charity_commission:123456), or recipient name."
        )
    )
    top_n: int = Field(default=10, ge=1, le=25)
    recent_limit: int = Field(default=5, ge=1, le=25)
    refresh: bool = Field(
        default=True,
        description=(
            "When recipient is an exact 360Giving Org ID or Charity Commission "
            "Soundings ID, refresh that recipient from the official 360Giving "
            "API before profiling. Ignored for name-only lookups."
        ),
    )


class GetRecipientProfileOutput(BaseModel):
    recipient_id: str | None = None
    recipient_external_id: str | None = None
    recipient_org_id: str | None = None
    recipient_name: str | None = None
    grants: int
    total_gbp: float
    average_gbp: float | None = None
    median_gbp: float | None = None
    earliest_award: str | None = None
    latest_award: str | None = None
    top_funders: list[RankedRecipientFunder] = Field(default_factory=list)
    top_programmes: list[RecipientProgramme] = Field(default_factory=list)
    grants_by_year: list[RecipientGrantYear] = Field(default_factory=list)
    recent_grants: list[RecipientGrantEvidence] = Field(default_factory=list)
    match_candidates: int = 0
    alternative_matches: list[RecipientAlternativeMatch] = Field(default_factory=list)
    refreshed_from_api: bool = False
    index_complete: bool
    index_coverage: str
    caveats: list[str] = Field(default_factory=list)


TOOL_NAME = "get_recipient_profile"
TOOL_DESCRIPTION = (
    "Profile a grant recipient using indexed 360Giving evidence: grant count, "
    "GBP total/average/median, award range, top funders, programmes, yearly "
    "funding history and recent grants. Exact recipient IDs can be refreshed "
    "from the official API before analysis."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetRecipientProfileInput.model_json_schema(),
        "output_schema": GetRecipientProfileOutput.model_json_schema(),
    }


def _api_recipient_id(value: str) -> str | None:
    if value.startswith("charity_commission:"):
        return "GB-CHC-" + value.split(":", 1)[1]
    if value.startswith(ORG_ID_PREFIXES) and " " not in value:
        return value
    return None


async def get_recipient_profile(
    input: GetRecipientProfileInput,
    engine: AsyncEngine,
) -> GetRecipientProfileOutput:
    refreshed = False
    caveats: list[str] = []
    api_id = _api_recipient_id(input.recipient)

    if input.refresh and api_id is not None:
        client = ThreeSixtyGivingClient()
        raw = [grant async for grant in client.iter_grants_received(api_id)]
        await GrantStore(engine).upsert_api_grants(raw)
        refreshed = True
    elif input.refresh:
        caveats.append(
            "Recipient was supplied as a name rather than an exact organisation "
            "ID, so no targeted API refresh was attempted."
        )

    profile = await GrantAnalytics(engine).recipient_profile(
        input.recipient,
        top_n=input.top_n,
        recent_limit=input.recent_limit,
    )
    status = await get_grant_index_status(engine)

    if not status["complete"] and not refreshed:
        caveats.append(
            "Profile is based on the currently indexed subset of 360Giving "
            "data, not yet the full corpus."
        )
    elif not status["complete"] and refreshed:
        caveats.append(
            "This recipient's API records were refreshed, but the wider local "
            "grant index is not yet full-corpus."
        )

    if profile["match_candidates"] > 1 and api_id is None:
        caveats.append(
            "Recipient name matched more than one indexed organisation; the "
            "strongest match is profiled and alternative matches are returned."
        )
    if profile["match_candidates"] == 0:
        caveats.append("No indexed 360Giving grants matched this recipient.")

    return GetRecipientProfileOutput(
        recipient_id=profile["recipient_id"],
        recipient_external_id=profile["recipient_external_id"],
        recipient_org_id=profile["recipient_org_id"],
        recipient_name=profile["recipient_name"],
        grants=profile["grants"],
        total_gbp=float(profile["total_gbp"] or 0),
        average_gbp=profile["average_gbp"],
        median_gbp=profile["median_gbp"],
        earliest_award=profile["earliest_award"],
        latest_award=profile["latest_award"],
        top_funders=[RankedRecipientFunder.model_validate(row) for row in profile["top_funders"]],
        top_programmes=[RecipientProgramme.model_validate(row) for row in profile["top_programmes"]],
        grants_by_year=[RecipientGrantYear.model_validate(row) for row in profile["grants_by_year"]],
        recent_grants=[RecipientGrantEvidence.model_validate(row) for row in profile["recent_grants"]],
        match_candidates=int(profile["match_candidates"]),
        alternative_matches=[
            RecipientAlternativeMatch.model_validate(row) for row in profile["alternative_matches"]
        ],
        refreshed_from_api=refreshed,
        index_complete=bool(status["complete"]),
        index_coverage=str(status["coverage"]),
        caveats=caveats,
    )
