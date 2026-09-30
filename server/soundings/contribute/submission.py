"""Observation submission — validate and persist to ``data.observation``.

Per the observations MVP plan
(docs/plans/2026-08-24-observations-mvp.md, Task 7).  The
``submit_observation`` coroutine validates that the referenced theme, place,
organisation, and (if provided) indicator exist before inserting the row.
A missing reference raises ``ValueError`` with a human-readable message; the
HTTP layer translates that into a 422 response.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.contracts.observation import ObservationSubmit


async def submit_observation(
    engine: AsyncEngine,
    observation: ObservationSubmit,
) -> UUID:
    """Validate references and insert ``observation`` into ``data.observation``.

    Returns the new row's UUID.  Raises ``ValueError`` if:

      - ``theme`` is not in ``catalogue.theme``
      - ``place_id`` is not in ``geography.place``
      - ``organisation_id`` is not in ``data.organisation``
      - ``indicator_key`` (when provided) is not in ``catalogue.indicator``
    """
    async with engine.begin() as conn:
        # ------------------------------------------------------------------
        # Validate theme exists.
        # ------------------------------------------------------------------
        theme_row = (
            await conn.execute(
                text("SELECT key FROM catalogue.theme WHERE key = :key"),
                {"key": observation.theme},
            )
        ).first()
        if theme_row is None:
            raise ValueError(f"Unknown theme: {observation.theme}")

        # ------------------------------------------------------------------
        # Validate place exists.
        # ------------------------------------------------------------------
        place_row = (
            await conn.execute(
                text("SELECT id FROM geography.place WHERE id = :id"),
                {"id": observation.place_id},
            )
        ).first()
        if place_row is None:
            raise ValueError(f"Unknown place: {observation.place_id}")

        # ------------------------------------------------------------------
        # Validate organisation exists.
        # ------------------------------------------------------------------
        org_row = (
            await conn.execute(
                text("SELECT id FROM data.organisation WHERE id = :id"),
                {"id": observation.organisation_id},
            )
        ).first()
        if org_row is None:
            raise ValueError(f"Unknown organisation: {observation.organisation_id}")

        # ------------------------------------------------------------------
        # Validate indicator_key if provided.
        # ------------------------------------------------------------------
        if observation.indicator_key is not None:
            indicator_row = (
                await conn.execute(
                    text("SELECT key FROM catalogue.indicator WHERE key = :key"),
                    {"key": observation.indicator_key},
                )
            ).first()
            if indicator_row is None:
                raise ValueError(f"Unknown indicator: {observation.indicator_key}")

        # ------------------------------------------------------------------
        # Insert the observation and return the new UUID.
        # ------------------------------------------------------------------
        result = await conn.execute(
            text(
                """
                INSERT INTO data.observation
                    (organisation_id, place_id, period_start, period_end, theme,
                     statement, indicator_key, value, unit, evidence_type,
                     methodology_note, confidence)
                VALUES
                    (:org_id, :place_id, :period_start, :period_end, :theme,
                     :statement, :indicator_key, :value, :unit, :evidence_type,
                     :methodology_note, :confidence)
                RETURNING id
                """
            ),
            {
                "org_id": observation.organisation_id,
                "place_id": observation.place_id,
                "period_start": observation.period_start,
                "period_end": observation.period_end,
                "theme": observation.theme,
                "statement": observation.statement,
                "indicator_key": observation.indicator_key,
                "value": observation.value,
                "unit": observation.unit,
                "evidence_type": observation.evidence_type,
                "methodology_note": observation.methodology_note,
                "confidence": observation.confidence,
            },
        )
        return cast(UUID, result.scalar_one())
