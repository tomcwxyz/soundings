"""Pydantic contracts for observation submission and retrieval.

Per the observations MVP plan (docs/plans/2026-08-24-observations-mvp.md).
These models back the POST /v1/observations endpoint and the
`get_observations` MCP tool.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

EvidenceType = Literal["quantitative", "qualitative"]
ConfidenceLevel = Literal["high", "medium", "low"]


class ObservationSubmit(BaseModel):
    """Input payload for POST /v1/observations."""

    organisation_id: str
    place_id: str
    period_start: date
    period_end: date | None = None
    theme: str
    statement: str = Field(min_length=10, max_length=1000)
    indicator_key: str | None = None
    value: float | None = None
    unit: str | None = None
    evidence_type: EvidenceType
    methodology_note: str | None = None
    confidence: ConfidenceLevel


class ObservationRecord(BaseModel):
    """Full observation record as stored and returned."""

    id: UUID
    organisation_id: str
    organisation_name: str
    place_id: str
    place_name: str
    period_start: date
    period_end: date | None
    theme: str
    statement: str
    indicator_key: str | None
    value: float | None
    unit: str | None
    evidence_type: EvidenceType
    methodology_note: str | None
    confidence: ConfidenceLevel
    submitted_at: datetime


class ObservationSummaryItem(BaseModel):
    """Per-theme summary entry for a place profile."""

    theme: str
    count: int
    latest_submission: datetime
    organisation_names: list[str]


class ObservationSummary(BaseModel):
    """Aggregated summary of observations for a place."""

    total_observations: int
    themes: list[ObservationSummaryItem]


class GetObservationsInput(BaseModel):
    """Input for the get_observations tool."""

    place_id: str | None = None
    theme: str | None = None
    indicator_key: str | None = None
    organisation_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class GetObservationsOutput(BaseModel):
    """Output for the get_observations tool."""

    observations: list[ObservationRecord] = Field(default_factory=list)
    total: int = 0
    summary: ObservationSummary | None = None
    caveats: list[str] = Field(default_factory=list)
