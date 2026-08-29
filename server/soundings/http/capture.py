"""Capture HTTP routes: consent management (this file), feedback (Task 21).

`POST /v1/capture/consent` records the user's consent choice and issues
the three cookies that the session middleware reads on every subsequent
request. We issue a fresh `session_id` on each consent POST — even for
`consent_level=none`, because spec §8.2 reserves the session_id for
rate-limiting purposes regardless of capture.
"""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from soundings.capture.consent import CONSENT_VERSION
from soundings.capture.context import AskerSector, ConsentLevel
from soundings.db.engine import get_engine
from soundings.http.session import SessionState

router = APIRouter(prefix="/v1/capture", tags=["capture"])


class ConsentRequest(BaseModel):
    consent_level: ConsentLevel
    asker_sector: AskerSector | None = None


class ConsentResponse(BaseModel):
    session_id: UUID
    consent_level: ConsentLevel
    consent_version: str
    asker_sector: AskerSector | None
    # Literal so OpenAPI shows the version explicitly.
    schema_version: Literal["v1"] = "v1"


_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def _set_consent_cookies(
    response: Response,
    *,
    session_id: UUID,
    consent_level: ConsentLevel,
    asker_sector: AskerSector | None,
) -> None:
    # SameSite=Lax is right for a UI on the same Cloudflare-tunnelled host
    # as the API. HttpOnly on the session cookie keeps it out of JS — the UI
    # only needs to know consent_level + sector, not the session_id.
    response.set_cookie(
        "soundings_session",
        str(session_id),
        max_age=_COOKIE_MAX_AGE,
        samesite="lax",
        path="/",
        httponly=True,
    )
    response.set_cookie(
        "soundings_consent",
        consent_level,
        max_age=_COOKIE_MAX_AGE,
        samesite="lax",
        path="/",
    )
    if asker_sector is not None:
        response.set_cookie(
            "soundings_sector",
            asker_sector,
            max_age=_COOKIE_MAX_AGE,
            samesite="lax",
            path="/",
        )
    else:
        # A level-only choice must not silently retain a sector from an
        # earlier session/consent choice.
        response.delete_cookie("soundings_sector", path="/", samesite="lax")


@router.post("/consent", response_model=ConsentResponse)
async def post_consent(body: ConsentRequest, response: Response) -> ConsentResponse:
    session_id = uuid4()
    _set_consent_cookies(
        response,
        session_id=session_id,
        consent_level=body.consent_level,
        asker_sector=body.asker_sector,
    )
    return ConsentResponse(
        session_id=session_id,
        consent_level=body.consent_level,
        consent_version=CONSENT_VERSION,
        asker_sector=body.asker_sector,
    )


class FeedbackRequest(BaseModel):
    question_record_id: UUID
    marked_useful: bool


class FeedbackResponse(BaseModel):
    ok: Literal[True] = True


@router.post("/feedback", response_model=FeedbackResponse)
async def post_feedback(body: FeedbackRequest, request: Request) -> FeedbackResponse:
    """Mark a question_record as useful (or not).

    Only the originating session can update its own records. We check
    the cookie session_id against the row's session_id and 403 on
    mismatch (or 403 if the caller has no session cookie at all).
    """
    session: SessionState = request.state.session
    if session.session_id is None:
        raise HTTPException(status_code=403, detail="missing session")

    async with get_engine().begin() as conn:
        row = (
            await conn.execute(
                text("SELECT session_id FROM corpus.question_record WHERE id = :id"),
                {"id": body.question_record_id},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="record not found")
        if row.session_id != session.session_id:
            raise HTTPException(status_code=403, detail="not your record")
        await conn.execute(
            text("UPDATE corpus.question_record SET marked_useful = :v WHERE id = :id"),
            {"v": body.marked_useful, "id": body.question_record_id},
        )
    return FeedbackResponse()
