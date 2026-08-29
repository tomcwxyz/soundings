"""Answer cache store — TTL-keyed cache for composed ask responses.

Normalises the question text (lowercase, stripped, collapsed whitespace)
and hashes it with the optional place_id to form a stable cache key.

On a cache hit, the stored SSE events are replayed — no Claude call, no
tool dispatch, no upstream API hits. On a miss, the orchestrator runs
normally and the caller stores the events via ``put``.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.db.models.answer_cache import AnswerCache

# Default TTL: 24 hours. Most underlying indicators update daily-to-quarterly,
# so a completed answer should survive ordinary reload/back-navigation without
# repeatedly invoking the model and tools. The TTL remains overridable per-put.
DEFAULT_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class CachedAnswer:
    events: list[dict[str, Any]]
    messages: list[dict[str, Any]] | None


def _normalise(question: str) -> str:
    """Lowercase, strip, collapse whitespace — so 'Stockton ' and 'stockton' match."""
    return re.sub(r"\s+", " ", question.strip().lower())


def question_hash(question: str, place_id: str | None = None) -> str:
    """SHA-256 of normalised question + place_id."""
    norm = _normalise(question)
    key = f"{norm}|{place_id or ''}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class AnswerCacheStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(self, question: str, place_id: str | None = None) -> CachedAnswer | None:
        """Return a cached completed answer if a fresh entry exists, else None.

        Increments hit_count on a successful read so we can measure
        cache effectiveness.
        """
        qhash = question_hash(question, place_id)
        now = datetime.now(tz=UTC)
        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(AnswerCache.events, AnswerCache.messages, AnswerCache.expires_at).where(
                    AnswerCache.question_hash == qhash,
                )
            )
            row = result.first()

        if row is None or row.expires_at <= now:
            return None

        # Fire-and-forget hit count increment (don't block the response)
        async with self._engine.begin() as conn:
            await conn.execute(
                update(AnswerCache)
                .where(AnswerCache.question_hash == qhash)
                .values(hit_count=AnswerCache.hit_count + 1)
            )

        return CachedAnswer(
            events=list(row.events),
            messages=list(row.messages) if row.messages is not None else None,
        )

    async def put(
        self,
        question: str,
        place_id: str | None,
        events: list[dict[str, Any]],
        messages: list[dict[str, Any]] | None = None,
        *,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        """Store events for the question + place_id with a TTL."""
        qhash = question_hash(question, place_id)
        now = datetime.now(tz=UTC)
        expires = now + ttl
        async with self._engine.begin() as conn:
            stmt = insert(AnswerCache).values(
                question_hash=qhash,
                question_text=question,
                place_id=place_id,
                events=events,
                messages=messages,
                created_at=now,
                expires_at=expires,
                hit_count=0,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[AnswerCache.question_hash],
                set_={
                    "question_text": stmt.excluded.question_text,
                    "place_id": stmt.excluded.place_id,
                    "events": stmt.excluded.events,
                    "messages": stmt.excluded.messages,
                    "created_at": stmt.excluded.created_at,
                    "expires_at": stmt.excluded.expires_at,
                    "hit_count": 0,  # reset on refresh
                },
            )
            await conn.execute(stmt)

    async def delete_expired(self) -> int:
        """Remove expired entries. Returns count of rows deleted."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM cache.answer_cache WHERE expires_at <= now()")
            )
        return result.rowcount

    async def invalidate(self, question: str, place_id: str | None = None) -> None:
        """Force-invalidate a single entry (e.g. after data refresh)."""
        qhash = question_hash(question, place_id)
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM cache.answer_cache WHERE question_hash = :h"),
                {"h": qhash},
            )
