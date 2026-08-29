"""Answer cache model — stores composed SSE events for identical questions.

Keyed by a SHA-256 hash of the normalised question text + place_id (if any).
The payload is the full list of SSE events (blocks + sources + done) so the
ask endpoint can replay them without calling Claude.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from soundings.db.models import Base


class AnswerCache(Base):
    __tablename__ = "answer_cache"
    __table_args__ = ({"schema": "cache"},)

    question_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    place_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    messages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hit_count: Mapped[int] = mapped_column(default=0)
