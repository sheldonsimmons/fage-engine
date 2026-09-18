"""
api/routes_livekit.py — mints short-lived LiveKit room-access tokens for
the real-time video avatar (agents/livekit_avatar_worker.py).

The frontend must never hold LIVEKIT_API_SECRET -- a token minted here,
scoped to one room and one participant identity, is the only thing it
ever sees. Same check_membership/workspace-scoping pattern used
everywhere else in this codebase (see routes_dashboard.py's
_check_reporting_access), so a token can't be minted for a workspace the
caller doesn't belong to.
"""

import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import check_membership
from database.db import get_db

router = APIRouter()


class LiveKitTokenRequest(BaseModel):
    workspace_id: Optional[str] = None


class LiveKitTokenResponse(BaseModel):
    token: str
    url: str
    room: str


@router.post("/token", response_model=LiveKitTokenResponse)
def create_livekit_token(
    body: LiveKitTokenRequest,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """
    Mints a token for a fresh, uniquely-named room -- one avatar
    conversation per room, matching agents/livekit_avatar_worker.py's
    entrypoint being dispatched once per room by the LiveKit Agents
    dispatch system. Soft-mode-gated the same way every other endpoint in
    this codebase is (see core/auth.py's AUTH_ENFORCEMENT_ENABLED
    docstring) -- a no-op today, real once enforcement is turned on.

    The requested workspace_id is encoded into the room name itself
    (ask-costpilot__<workspace_id>__<random>) -- confirmed live 2026-09-19
    this was a real gap, not just a v1 simplification: without it, the
    LiveKit voice agent had no way to know which workspace the browser
    was actually looking at, and silently answered from whatever a fixed
    env var happened to be set to instead, sometimes a workspace with far
    less data than the one the person meant. agents/livekit_avatar_worker.py
    parses this back out of ctx.room.name.
    """
    if body.workspace_id:
        check_membership(db, authorization, body.workspace_id, "use_ask_costpilot")

    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    livekit_url = os.getenv("LIVEKIT_URL")
    if not api_key or not api_secret or not livekit_url:
        raise HTTPException(
            status_code=503,
            detail="The real-time video avatar isn't configured yet (LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET).",
        )

    from livekit import api

    # Alphanumeric + hyphen only, matching every real workspace_id format
    # seen in this app ("default", "SIM-HISTORICAL-2Y", "4BE43240A6674314")
    # -- keeps "__" unambiguous as the room-name delimiter regardless of
    # what a workspace_id contains.
    safe_workspace_id = re.sub(r"[^A-Za-z0-9-]", "", body.workspace_id or "default") or "default"
    room_name = f"ask-costpilot__{safe_workspace_id}__{uuid.uuid4().hex[:8]}"
    participant_identity = f"user-{uuid.uuid4().hex[:8]}"
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(participant_identity)
        .with_name("Ask CostPilot user")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    return LiveKitTokenResponse(token=token, url=livekit_url, room=room_name)
