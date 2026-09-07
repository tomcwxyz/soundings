"""find_organisations_in_place tool — resolve organisations operating in a place.

Per spec §4.6. Mixed-mode dispatch:
- England/Wales → SELECT from data.organisation (CC loader-populated)
- Scotland/NI → FTC passthrough adapter
- Once a full GrantNav corpus exists, funded-only filtering and recent-grant
  enrichment use the local grant index rather than live 360Giving fan-out.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from soundings.contracts.organisation import OrganisationRef
from soundings.contracts.source_ref import SourceRef
from soundings.grants.status import has_complete_grant_index

_log = logging.getLogger(__name__)


class FindOrganisationsInPlaceInput(BaseModel):
    place_id: str = Field(description="Canonical geography place ID (e.g. ltla24:E06000004)")
    activity_filter: list[str] | None = Field(
        default=None,
        description=(
            "Optional cause keywords. When set, only charities whose name or"
            " charitable objects match one of these terms (case-insensitive"
            " substring) are returned — e.g. ['food bank', 'food poverty',"
            " 'hunger']. Supply several near-synonyms for recall. Leave unset"
            " to return all organisations in the place."
        ),
    )
    funded_only: bool = Field(
        default=False,
        description=(
            "Only return organisations with indexed 360Giving grants. Applied "
            "when the full GrantNav corpus is available for local CC-backed results."
        ),
    )
    limit: int = Field(
        default=50,
        description="Maximum number of organisations to return",
    )


class FindOrganisationsInPlaceOutput(BaseModel):
    organisations: list[OrganisationRef] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    partial: bool = Field(default=False, description="True if any leg of the query failed")


TOOL_NAME = "find_organisations_in_place"
TOOL_DESCRIPTION = (
    "Find organisations (charities, nonprofits) operating in a given UK place. "
    "For England/Wales returns Charity Commission data; for Scotland/NI queries "
    "Find That Charity. When the full GrantNav corpus is available, can filter "
    "to funded organisations and include recent 360Giving grants from the local index."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": FindOrganisationsInPlaceInput.model_json_schema(),
        "output_schema": FindOrganisationsInPlaceOutput.model_json_schema(),
    }


def _uses_ftc_place(place_id: str) -> bool:
    return place_id.startswith(
        (
            "country:S",
            "country:NI",
            "ltla24:S",
            "utla24:S",
            "ltla24:N",
            "utla24:N",
        )
    )


async def _candidate_count(engine: Any, place_id: str) -> int:
    """Upper bound for the local organisation query before funded filtering."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT o.id) AS n
                    FROM data.organisation o
                    LEFT JOIN data.organisation_operates_in oi
                      ON oi.organisation_id = o.id
                    WHERE o.registered_address_place_id = :pid
                       OR oi.place_id = :pid
                    """
                ),
                {"pid": place_id},
            )
        ).first()
    return int(row.n) if row else 0


async def _funded_org_ids(engine: Any, place_id: str) -> set[str]:
    """Return local CC organisation IDs with at least one indexed grant."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT o.id
                    FROM data.organisation o
                    LEFT JOIN data.organisation_operates_in oi
                      ON oi.organisation_id = o.id
                    WHERE (o.registered_address_place_id = :pid OR oi.place_id = :pid)
                      AND EXISTS (
                          SELECT 1
                          FROM data.grant_record g
                          WHERE g.source_id = 'threesixtygiving'
                            AND (
                                g.recipient_org_id = o.id
                                OR (
                                    o.id LIKE 'charity_commission:%'
                                    AND g.recipient_external_id =
                                        'GB-CHC-' || split_part(o.id, ':', 2)
                                )
                            )
                      )
                    """
                ),
                {"pid": place_id},
            )
        ).all()
    return {str(row.id) for row in rows}


def _dedupe_sources(sources: list[SourceRef]) -> list[SourceRef]:
    seen: set[str] = set()
    out: list[SourceRef] = []
    for source in sources:
        if source.source_id in seen:
            continue
        seen.add(source.source_id)
        out.append(source)
    return out


async def find_organisations_in_place(
    input: FindOrganisationsInPlaceInput,
    orchestrator: Any,
) -> FindOrganisationsInPlaceOutput:
    """Tool handler with coverage-aware local grant behaviour.

    Before the first successful full GrantNav import, the organisation list
    remains available but ``funded_only`` is not applied and grant enrichment
    stays disabled. This avoids treating a partial write-through index as a
    complete funding universe.
    """
    engine = orchestrator._engine
    full_index = await has_complete_grant_index(engine)
    ftc_place = _uses_ftc_place(input.place_id)

    caveats: list[str] = []
    partial = False

    if input.funded_only and full_index and not ftc_place:
        # The orchestrator's CC query sorts by income before LIMIT. Ask it for
        # the complete local candidate set, then filter, so a funded charity is
        # never hidden merely because an unfunded higher-income charity consumed
        # the caller's requested limit.
        candidate_limit = max(input.limit, await _candidate_count(engine, input.place_id))
        result = await orchestrator.find_organisations_in_place(
            place_id=input.place_id,
            activity_filter=input.activity_filter,
            funded_only=False,
            limit=candidate_limit,
            enrich_grants=False,
        )
        funded_ids = await _funded_org_ids(engine, input.place_id)
        organisations = [org for org in result.organisations if org.id in funded_ids][: input.limit]
        sources = list(result.sources)
        caveats.extend(result.caveats)
        partial = result.partial
    else:
        result = await orchestrator.find_organisations_in_place(
            place_id=input.place_id,
            activity_filter=input.activity_filter,
            funded_only=False,
            limit=input.limit,
            enrich_grants=False,
        )
        organisations = list(result.organisations)
        sources = list(result.sources)
        caveats.extend(result.caveats)
        partial = result.partial

        if input.funded_only and not full_index:
            caveats.append(
                "funded_only was not applied because the full GrantNav index is not yet available"
            )
            partial = True
        elif input.funded_only and ftc_place:
            caveats.append(
                "funded_only is not yet available for Find That Charity-backed "
                "Scotland/Northern Ireland organisation results"
            )
            partial = True

    # Grant enrichment is now safe to restore after the full corpus bootstrap:
    # ThreeSixtyGivingAdapter.recent_grants_for_org reads the local index and
    # performs no upstream HTTP calls in this state. Enrich only the final list.
    if full_index and organisations and not ftc_place:
        grants_result = await orchestrator._enrich_with_grants(input.place_id, organisations)
        for org in organisations:
            org.recent_grants = grants_result.grants_by_org.get(org.id, [])
        sources.extend(grants_result.sources)
        caveats.extend(grants_result.caveats)
        partial = partial or grants_result.partial

        try:
            grants_adapter = orchestrator._registry.adapter_for_source("threesixtygiving")
            sources.append(
                grants_adapter.get_source_ref(
                    retrieved_at=datetime.now(tz=UTC),
                    cache_status="cached",
                )
            )
        except Exception:
            # The enrichment result already carries its own source when grants
            # were returned. Source metadata should not make the tool fail.
            _log.debug("Could not add 360Giving source metadata", exc_info=True)
    elif not full_index:
        caveats.append(
            "Per-organisation grant detail is unavailable until the full GrantNav index is loaded."
        )

    return FindOrganisationsInPlaceOutput(
        organisations=organisations[: input.limit],
        sources=_dedupe_sources(sources),
        caveats=caveats,
        partial=partial,
    )
