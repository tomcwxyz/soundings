"""Magic-link authentication for observation contributors.

A contributor (someone at a registered organisation) submits their email +
organisation_id; we create a ``contribution.contributor_session`` row storing
a SHA-256 *hash* of a random token and send the raw token to the contributor
via an ``EmailSender``.  In the MVP the sender is a stub that logs the token;
real email delivery is deferred.

When the contributor clicks the magic link, ``POST /v1/contribute/verify-link``
exchanges the raw token for a signed cookie (``soundings_contrib_session``)
that carries the ``organisation_id``.  The cookie is signed with an HMAC of a
secret key so it cannot be forged.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast, runtime_checkable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)

# Token lifetime: a magic link is only valid for 15 minutes after issue.
TOKEN_TTL_MINUTES = 15


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of the raw token.  We store only the hash so a DB
    leak doesn't expose valid tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@runtime_checkable
class EmailSender(Protocol):
    """Pluggable email sender.  The MVP stub logs the token; a real
    implementation would dispatch a templated email with a magic link."""

    async def send(self, to: str, token: str) -> None: ...


class StubEmailSender:
    """Default EmailSender used in dev/test — logs the token instead of
    sending a real email."""

    async def send(self, to: str, token: str) -> None:
        log.info("magic-link stub: to=%s token=%s", to, token)


class MagicLinkService:
    """Creates and verifies magic-link contributor sessions.

    Token storage uses SHA-256 hashing (never store the raw token).  A
    successful verification marks the session ``used_at`` so it can't be
    replayed.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        email_sender: EmailSender,
        *,
        cookie_secret: str = "soundings-dev-cookie-secret-change-me",  # noqa: S107
    ) -> None:
        self._engine = engine
        self._sender = email_sender
        self._cookie_secret = cookie_secret

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    async def create_session(self, organisation_id: str, email: str) -> str:
        """Create a contributor_session row, dispatch the magic link, and
        return the raw token (only the caller — not the DB — sees it)."""
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        now = datetime.now(tz=UTC)
        expires_at = now + timedelta(minutes=TOKEN_TTL_MINUTES)
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO contribution.contributor_session "
                    "(organisation_id, email, token_hash, created_at, expires_at) "
                    "VALUES (:oid, :email, :th, :now, :exp)"
                ),
                {
                    "oid": organisation_id,
                    "email": email,
                    "th": token_hash,
                    "now": now,
                    "exp": expires_at,
                },
            )
        await self._sender.send(email, token)
        return token

    async def verify_token(self, token: str) -> str | None:
        """Verify a raw token against stored hashes.

        Returns the ``organisation_id`` on success (and marks the row used),
        or ``None`` if the token is unknown, already used, or expired.
        """
        token_hash = _hash_token(token)
        now = datetime.now(tz=UTC)
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT id, organisation_id, expires_at, used_at "
                        "FROM contribution.contributor_session "
                        "WHERE token_hash = :th"
                    ),
                    {"th": token_hash},
                )
            ).first()
            if row is None:
                return None
            if row.used_at is not None:
                return None
            if row.expires_at <= now:
                return None
            await conn.execute(
                text("UPDATE contribution.contributor_session SET used_at = :now WHERE id = :id"),
                {"now": now, "id": row.id},
            )
            return cast(str, row.organisation_id)

    # ------------------------------------------------------------------ #
    # cookie signing / verification
    # ------------------------------------------------------------------ #

    def sign_cookie_value(self, organisation_id: str) -> str:
        """Produce a signed cookie payload ``<org_id>.<hmac>``."""
        sig = hmac.new(
            self._cookie_secret.encode("utf-8"),
            organisation_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{organisation_id}.{sig}"

    def verify_cookie_value(self, value: str) -> str | None:
        """Verify a signed cookie payload and return the organisation_id, or
        ``None`` if the signature is invalid."""
        if "." not in value:
            return None
        organisation_id, sig = value.rsplit(".", 1)
        expected = hmac.new(
            self._cookie_secret.encode("utf-8"),
            organisation_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(sig, expected):
            return organisation_id
        return None

    # Expose the hasher for tests that need to compute a hash directly.
    _hash = staticmethod(_hash_token)
