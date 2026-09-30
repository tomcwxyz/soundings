"""HTTP routes for observation submission and retrieval.

  POST /v1/observations  — authenticated submission of a new observation

This is a separate router from ``contribute.py`` so the path is
``/v1/observations`` (not ``/v1/contribute/observations``).  Auth still
flows through the contributor magic-link cookie
(``soundings_contrib_session``) signed by ``MagicLinkService``.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from soundings.contracts.observation import ObservationSubmit
from soundings.contribute.submission import submit_observation
from soundings.http.contribute import COOKIE_NAME

router = APIRouter(prefix="/v1", tags=["observations"])


class ObservationSubmitOutput(BaseModel):
    """Successful submission response body."""

    status: Literal["accepted"] = "accepted"
    observation_id: str


def _get_service(request: Request):  # type: ignore[no-untyped-def]
    service = getattr(request.app.state, "magic_link_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="contribute auth not configured")
    return service


@router.post("/observations", response_model=ObservationSubmitOutput, status_code=201)
async def submit_observation_route(
    observation: ObservationSubmit, request: Request
) -> ObservationSubmitOutput:
    """Validate and store an observation.

    Requires a valid ``soundings_contrib_session`` cookie whose
    organisation_id matches ``observation.organisation_id``.
    """
    service = _get_service(request)

    # ------------------------------------------------------------------ #
    # 1. Read the contributor session cookie.
    # ------------------------------------------------------------------ #
    cookie_value = request.cookies.get(COOKIE_NAME)
    if not cookie_value:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # ------------------------------------------------------------------ #
    # 2. Verify the HMAC signature to extract the organisation_id.
    # ------------------------------------------------------------------ #
    cookie_org_id = service.verify_cookie_value(cookie_value)
    if cookie_org_id is None:
        raise HTTPException(status_code=401, detail="Invalid session cookie")

    # ------------------------------------------------------------------ #
    # 3. The cookie's organisation_id must match the submission's org.
    # ------------------------------------------------------------------ #
    if cookie_org_id != observation.organisation_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot submit on behalf of another organisation",
        )

    # ------------------------------------------------------------------ #
    # 4. Validate + insert.  ValueError -> 422 with the message.
    # ------------------------------------------------------------------ #
    try:
        obs_id: UUID = await submit_observation(request.app.state.engine, observation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ObservationSubmitOutput(observation_id=str(obs_id))
