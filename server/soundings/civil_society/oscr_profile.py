"""Scottish civil society profile from postcode-matched *registered addresses*.

OSCR does not provide a verified council-by-council operating-area dataset.
The schema's headline total is therefore explicitly labelled as a partial
registered-address count, never described as operating in the council.
"""

from collections import Counter
from datetime import UTC, datetime
from statistics import mean, median

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.contracts.civil_society import (
    CauseAreaCount,
    CivilSocietyProfile,
    IncomeBucket,
    NotableOrg,
    NotableOrgs,
    RegistrationCohort,
)
from soundings.contracts.source_ref import SourceRef

BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("<10k", 0, 10_000),
    ("10k-100k", 10_000, 100_000),
    ("100k-1m", 100_000, 1_000_000),
    ("1m-10m", 1_000_000, 10_000_000),
    ("10m+", 10_000_000, None),
)
UNSUPPORTED_SMALL_AREAS = ("dz22:", "dz11:", "iz22:", "iz11:")


async def build_oscr_civil_society_profile(
    engine: AsyncEngine,
    place_id: str,
    keywords: list[str] | None = None,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
) -> CivilSocietyProfile:
    """Return a clearly partial profile, never silently synthesize operating counts."""
    caveats = [
        "Scottish OSCR figures are postcode-matched registered addresses, NOT "
        "a count of charities delivering services here.",
        "Unmatched postcodes and cross-register duplicate registrations are not "
        "reconciled, so this is not a complete unique-charity count.",
        "Annual registration cohorts include only currently registered charities, "
        "not all charities ever registered in each year.",
        "© Crown Copyright and database right. Contains Scottish Charity Register "
        "information supplied by OSCR under the Open Government Licence v3.0.",
    ]
    normalised = [k.strip() for k in (keywords or []) if k.strip()]
    if place_id.startswith(UNSUPPORTED_SMALL_AREAS):
        return CivilSocietyProfile(
            place_id=place_id,
            coverage_basis="unavailable",
            filter_keywords=normalised,
            caveats=[
                *caveats,
                "OSCR register coverage is not yet resolved to Data Zones or "
                "Intermediate Zones. Zero in this response means not measured.",
            ],
            partial=True,
        )

    async with engine.connect() as conn:
        has_register = (
            await conn.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM data.organisation "
                    "WHERE source_id = 'oscr' "
                    "AND raw->>'in_current_extract' = 'true')"
                )
            )
        ).scalar_one()
        if not has_register:
            return CivilSocietyProfile(
                place_id=place_id,
                coverage_basis="unavailable",
                filter_keywords=normalised,
                caveats=[
                    *caveats,
                    "The OSCR register has not been successfully imported: "
                    "zero means not measured, not no charities.",
                ],
                partial=True,
            )

        patterns = [f"%{keyword}%" for keyword in normalised]
        rows = (
            await conn.execute(
                text(
                    "SELECT o.id, o.name, o.classification, o.raw "
                    "FROM data.organisation o "
                    "WHERE o.source_id = 'oscr' "
                    "AND o.raw->>'in_current_extract' = 'true' "
                    "AND (o.registered_address_place_id = :pid "
                    "  OR EXISTS ("
                    "   SELECT 1 FROM geography.current_place_hierarchy h "
                    "   WHERE h.child_id = o.registered_address_place_id "
                    "   AND h.parent_id = :pid)) "
                    "AND (NOT CAST(:has_keywords AS boolean) "
                    "  OR o.name ILIKE ANY(CAST(:patterns AS text[])) "
                    "  OR array_to_string(o.classification,' ') "
                    "     ILIKE ANY(CAST(:patterns AS text[])))"
                ),
                {
                    "pid": place_id,
                    "has_keywords": bool(patterns),
                    "patterns": patterns,
                },
            )
        ).all()

    incomes: list[float] = []
    categories: Counter[str] = Counter()
    registration_years: Counter[int] = Counter()
    valid_years: list[tuple[str, int, str, str]] = []
    candidates: list[tuple[str, str, float, str | None]] = []
    for row in rows:
        raw = row.raw or {}
        income = raw.get("latest_income")
        value = float(income) if income is not None else None
        if value is not None and value >= 0:
            incomes.append(value)
            candidates.append((row.id, row.name, value, raw.get("date_of_registration")))
        for cause in set(row.classification or []):
            if cause:
                categories[cause] += 1
        reg = raw.get("date_of_registration")
        if reg and len(reg) >= 4 and reg[:4].isdigit():
            yr = int(reg[:4])
            registration_years[yr] += 1
            valid_years.append((reg, yr, row.id, row.name))

    def notable(entry: tuple[str, int, str, str] | None) -> NotableOrg | None:
        if entry is None:
            return None
        reg, year, org_id, name = entry
        return NotableOrg(id=org_id, name=name, date_of_registration=reg, year_registered=year)

    old = min(valid_years, default=None)
    newest = max(valid_years, default=None)
    largest = max(candidates, key=lambda record: record[2], default=None)
    total_income = sum(incomes)
    highest = sorted(incomes, reverse=True)[:3]
    notable_orgs = NotableOrgs(
        oldest=notable(old),
        newest=notable(newest),
        largest=(
            NotableOrg(
                id=largest[0],
                name=largest[1],
                latest_income=largest[2],
                date_of_registration=largest[3],
            )
            if largest
            else None
        ),
        income_concentration_top3_pct=(
            100 * sum(highest) / total_income if total_income > 0 else None
        ),
        income_concentration_top3_total=sum(highest) if incomes else None,
    )
    cohorts = [
        RegistrationCohort(year=year, registered=count, removed=0, net=count)
        for year, count in sorted(registration_years.items())
        if (year_from is None or year >= year_from) and (year_to is None or year <= year_to)
    ]
    buckets = [
        IncomeBucket(
            label=label,
            lower=lower,
            upper=upper,
            count=sum(
                1 for income in incomes if income >= lower and (upper is None or income < upper)
            ),
        )
        for label, lower, upper in BUCKETS
    ]
    source = SourceRef(
        source_id="oscr",
        source_label="Scottish Charity Register (OSCR)",
        publisher="Office of the Scottish Charity Regulator",
        licence="OGL-UK-3.0",
        retrieved_at=datetime.now(tz=UTC),
        cache_status="cached",
    )
    return CivilSocietyProfile(
        place_id=place_id,
        total_organisations=len(rows),
        registered_address_count=len(rows),
        coverage_basis="registered_address",
        with_reported_income=len(incomes),
        median_income=median(incomes) if incomes else None,
        mean_income=mean(incomes) if incomes else None,
        income_buckets=buckets,
        registration_cohort=cohorts,
        filter_keywords=normalised,
        sources=[source],
        caveats=caveats,
        partial=True,
        notable=notable_orgs,
        cause_area_distribution=[
            CauseAreaCount(label=label, count=count) for label, count in categories.most_common(10)
        ],
    )
