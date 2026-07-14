"""In-memory conversation store for multi-turn follow-up questions.

Stores the full Claude message list (user + assistant + tool_result +
thinking blocks) after each turn so follow-up questions can continue
from the existing context.

TTL is 30 minutes from last activity. Conversations are lost on server
restart — acceptable for MVP; users can re-ask.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_TTL_MINUTES = 30


@dataclass
class Conversation:
    messages: list[dict[str, Any]]
    place_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    last_active: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


class ConversationStore:
    """In-memory conversation store with TTL."""

    def __init__(self, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> None:
        self._store: dict[str, Conversation] = {}
        self._ttl = timedelta(minutes=ttl_minutes)

    def create(self, place_id: str | None = None) -> str:
        """Create a new conversation, returning its UUID."""
        self._cleanup_expired()
        conv_id = str(uuid.uuid4())
        self._store[conv_id] = Conversation(messages=[], place_id=place_id)
        return conv_id

    def get(self, conversation_id: str) -> Conversation | None:
        """Return the conversation, or None if not found or expired."""
        self._cleanup_expired()
        conv = self._store.get(conversation_id)
        if conv is None:
            return None
        # Check TTL against last_active
        if datetime.now(tz=UTC) - conv.last_active > self._ttl:
            del self._store[conversation_id]
            return None
        return conv

    def append_messages(self, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        """Append messages to a conversation and update last_active."""
        conv = self._store.get(conversation_id)
        if conv is None:
            return
        conv.messages.extend(messages)
        conv.last_active = datetime.now(tz=UTC)

    def update_place_id(self, conversation_id: str, place_id: str) -> None:
        """Update the place_id for a conversation (e.g. when the first
        question resolved a place via find_place rather than a request param)."""
        conv = self._store.get(conversation_id)
        if conv is None:
            return
        conv.place_id = place_id
        conv.last_active = datetime.now(tz=UTC)

    def _cleanup_expired(self) -> None:
        """Remove conversations whose TTL has expired."""
        now = datetime.now(tz=UTC)
        expired = [cid for cid, conv in self._store.items() if now - conv.last_active > self._ttl]
        for cid in expired:
            del self._store[cid]
