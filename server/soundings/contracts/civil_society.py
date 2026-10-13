"""CivilSocietyProfile — response shape for `get_civil_society_profile`.

Aggregate view of registered charities operating in a place: total,
income distribution, median/mean size, and a registration-cohort
trend. Inputs come from `data.organisation` (CC bulk register) joined
to `data.organisation_operates_in`.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from soundings.contracts.source_ref import SourceRef


class IncomeBucket(BaseModel):
    label: str = Field(description="Human-readable bucket label, e.g. '<10k', '10k-100k'.")
    lower: float = Field(ge=0, description="Inclusive lower bound, GBP.")
    upper: float | None = Field(
        default=None,
        description="Exclusive upper bound, GBP. None for the open-ended top bucket.",
    )
    count: int = Field(ge=0)


class RegistrationCohort(BaseModel):
    year: int
    registered: int = Field(ge=0, description="Charities first registered in this year.")
    removed: int = Field(ge=0, description="Charities removed in this year.")
    net: int = Field(description="`registered` - `removed`.")

    @model_validator(mode="after")
    def _check_net(self) -> Self:
        if self.net != self.registered - self.removed:
            raise ValueError("net must equal registered - removed")
        return self


class FunderSummary(BaseModel):
    name: str = Field(description="Funder organisation name.")
    grant_count: int = Field(ge=0)
    total_gbp: float = Field(ge=0, description="Sum of GBP grants from this funder.")


class GrantYearSummary(BaseModel):
    year: int = Field(description="Calendar year of the grant award date.")
    grant_count: int = Field(ge=0)
    total_gbp: float = Field(ge=0, description="Sum of GBP grants awarded in this year.")


class NotableOrg(BaseModel):
    id: str = Field(description="`{regulator}:{registration_number}` namespaced id.")
    name: str
    register_url: str | None = Field(default=None)
    latest_income: float | None = Field(
        default=None, description="Latest reported annual income in GBP, if available."
    )
    date_of_registration: str | None = Field(
        default=None, description="ISO date string of first registration, if known."
    )
    year_registered: int | None = Field(
        default=None, description="Calendar year of first registration, if known."
    )


class NotableOrgs(BaseModel):
    oldest: NotableOrg | None = Field(default=None)
    newest: NotableOrg | None = Field(default=None)
    largest: NotableOrg | None = Field(
        default=None, description="Charity with the highest `latest_income`."
    )
    income_concentration_top3_pct: float | None = Field(
        default=None,
        description="Combined income share (0-100) of the top three charities by income.",
    )
    income_concentration_top3_total: float | None = Field(
        default=None, description="Combined GBP income of the top three charities."
    )


class CauseAreaCount(BaseModel):
    label: str = Field(description="Cause-area label, e.g. 'Education', 'Health'.")
    count: int = Field(ge=0)


class CivilSocietyProfile(BaseModel):
    place_id: str
    coverage_basis: Literal["operating_area", "registered_address", "unavailable"] = Field(
        default="operating_area",
        description=(
            "How the headline organisation count was established. The Scottish OSCR "
            "postcode match measures registered addresses, not service delivery. "
            "'unavailable' means the geography/source is not covered, not zero charities."
        ),
    )
    total_organisations: int = Field(
        ge=0,
        description=(
            "Charities counted for this area. Interpret using coverage_basis: "
            "operating_area reflects a declared operating area, registered_address "
            "reflects a postcode-matched office, and unavailable is NOT a zero count."

        ),
    )
    registered_address_count: int = Field(
        ge=0,
        description=(
            "Charities with their *registered address* in this place —"
            " based on postcode resolution. A subset of"
            " total_organisations. Always <= total_organisations."
        ),
    )
    with_reported_income: int = Field(
        ge=0,
        description=(
            "Subset of total_organisations that have a non-null `latest_income` on"
            " their CC return. Median/mean are computed over this subset."
        ),
    )
    median_income: float | None = Field(
        default=None, description="Median GBP of `latest_income` over reporting charities."
    )
    mean_income: float | None = Field(
        default=None, description="Mean GBP of `latest_income` over reporting charities."
    )
    income_buckets: list[IncomeBucket] = Field(default_factory=list)
    registration_cohort: list[RegistrationCohort] = Field(
        default_factory=list,
        description="One row per year, oldest first. Filtered to year_from/year_to when set.",
    )
    top_funders: list[FunderSummary] = Field(
        default_factory=list,
        description=(
            "Top funders by total GBP awarded to charities in this place in the"
            " last 12 months (360Giving). Empty when no grant data is available."
        ),
    )
    grants_by_year: list[GrantYearSummary] = Field(
        default_factory=list,
        description=(
            "Total GBP grants and grant count per calendar year, from 360Giving."
            " Covers the full available history (not just 12 months). Empty when"
            " no grant data is available or the 360G lookup timed out."
        ),
    )
    filter_keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Cause keywords the profile was filtered by, if any. Empty means the"
            " profile covers every charity in the place. When set, all counts and"
            " distributions reflect only charities whose name or charitable objects"
            " match one of these keywords."
        ),
    )
    sources: list[SourceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    partial: bool = Field(default=False)
    notable: NotableOrgs = Field(default_factory=NotableOrgs)
    cause_area_distribution: list[CauseAreaCount] = Field(
        default_factory=list,
        description=(
            "Count of charities per cause-area label. Empty when no cause-area"
            " classification is available."
        ),
    )
