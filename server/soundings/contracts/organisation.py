"""OrganisationRef + GrantRef — civil-society response shapes.

Per spec §4.6. Returned by `find_organisations_in_place` and, by
extension, every passthrough adapter that overrides
`fetch_organisations` (CC, FTC in Phase 4; org self-registration in
v2). `recent_grants` is enriched by the 360Giving adapter, but
remains optional so adapters that don't carry grants leave it empty.
"""

from pydantic import BaseModel, Field

from soundings.contracts.source_ref import SourceRef


class GrantRef(BaseModel):
    funder: str
    amount: float
    currency: str = "GBP"
    date: str  # ISO date string from the upstream record
    purpose: str | None = None
    source: SourceRef


class OrganisationRef(BaseModel):
    id: str  # `{regulator}:{registration_number}` namespacing
    name: str
    classification: list[str] = Field(default_factory=list)
    registered_address_place_id: str | None = None
    operates_in_place_ids: list[str] = Field(default_factory=list)
    operates_in_place_names: list[str] = Field(
        default_factory=list,
        description="Place names corresponding to operates_in_place_ids, for display.",
    )
    recent_grants: list[GrantRef] = Field(default_factory=list)
    latest_income: float | None = Field(
        default=None,
        description="Latest reported annual income in GBP, if available.",
    )
    register_url: str | None = Field(
        default=None,
        description="Direct link to the regulator's public register page, if constructible.",
    )
    date_of_registration: str | None = Field(
        default=None,
        description="ISO date string of first registration, if known.",
    )
    postcode: str | None = Field(
        default=None,
        description="Registered-address postcode, if available.",
    )
    source: SourceRef
    methodology_note: str | None = None
