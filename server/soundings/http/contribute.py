"""HTTP routes for the magic-link contributor auth flow.

  POST /v1/contribute/request-link  — issues a (stub) magic link
  POST /v1/contribute/verify-link   — exchanges a token for a signed cookie

Both routes pull a ``MagicLinkService`` off ``request.app.state.magic_link_service``.
If the service is not configured (e.g. the app started without the contribute
feature enabled) the routes return 503.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(prefix="/v1/contribute", tags=["contribute"])

COOKIE_NAME = "soundings_contrib_session"
COOKIE_MAX_AGE = 86400  # 24 hours


class RequestLinkInput(BaseModel):
    organisation_id: str
    email: str


class RequestLinkOutput(BaseModel):
    status: Literal["link_sent"] = "link_sent"


class VerifyLinkInput(BaseModel):
    token: str


class VerifyLinkOutput(BaseModel):
    status: Literal["verified"] = "verified"
    organisation_id: str


def _get_service(request: Request):  # type: ignore[no-untyped-def]
    service = getattr(request.app.state, "magic_link_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="contribute auth not configured")
    return service


@router.post("/request-link", response_model=RequestLinkOutput)
async def request_link(body: RequestLinkInput, request: Request) -> RequestLinkOutput:
    service = _get_service(request)
    engine = request.app.state.engine

    # Verify the org exists before creating a session.  We do NOT reveal
    # whether the org exists — return the same response either way so the
    # endpoint can't be used to enumerate organisation IDs.
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT 1 FROM data.organisation WHERE id = :oid"),
                {"oid": body.organisation_id},
            )
        ).first()

    if row is not None:
        await service.create_session(body.organisation_id, str(body.email))

    return RequestLinkOutput()


class SignupInput(BaseModel):
    name: str
    email: str
    primary_place_id: str


class SignupOutput(BaseModel):
    status: Literal["created", "exists"]
    organisation_id: str


@router.post("/signup", response_model=SignupOutput, status_code=201)
async def signup(body: SignupInput, request: Request, response: Response) -> SignupOutput:
    """Create a lightweight ``data.organisation`` row for an org that is not
    in any official register, so it can submit observations.

    Idempotent: if an org with the same name + ``ctx.manual_signup`` source
    already exists, returns 200 with ``status="exists"``.
    """
    engine = request.app.state.engine
    source_id = "ctx.manual_signup"

    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                text(
                    "SELECT id FROM data.organisation WHERE name = :name AND source_id = :source_id"
                ),
                {"name": body.name, "source_id": source_id},
            )
        ).first()

        if existing is not None:
            response.status_code = 200
            return SignupOutput(status="exists", organisation_id=existing.id)

        org_id = f"ctx.{body.name.lower().replace(' ', '_')[:40]}"
        await conn.execute(
            text(
                "INSERT INTO data.organisation "
                "(id, name, classification, source_id, retrieved_at, raw) VALUES "
                "(:id, :name, ARRAY[]::varchar[], :source_id, now(), '{}'::jsonb)"
            ),
            {"id": org_id, "name": body.name, "source_id": source_id},
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation_operates_in "
                "(organisation_id, place_id) VALUES (:oid, :pid) "
                "ON CONFLICT DO NOTHING"
            ),
            {"oid": org_id, "pid": body.primary_place_id},
        )

    return SignupOutput(status="created", organisation_id=org_id)


@router.post("/verify-link", response_model=VerifyLinkOutput)
async def verify_link(
    body: VerifyLinkInput, request: Request, response: Response
) -> VerifyLinkOutput:
    service = _get_service(request)
    organisation_id = await service.verify_token(body.token)
    if organisation_id is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")

    signed = service.sign_cookie_value(organisation_id)
    response.set_cookie(
        COOKIE_NAME,
        signed,
        max_age=COOKIE_MAX_AGE,
        samesite="strict",
        path="/",
        httponly=True,
        secure=True,
    )
    return VerifyLinkOutput(organisation_id=organisation_id)
