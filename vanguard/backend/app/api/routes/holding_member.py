"""Holding-site member endpoints — Aria memory & chat keyed by Privy DID."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from aria_core import repertoire_db
from aria_core.brain import aria_brain
from aria_core.locale import LANG_FR
from aria_core.member_memory import (
    build_member_greeting,
    get_member_profile,
    member_visitor_id,
    remember_fact,
    touch_member,
)
from aria_core.models import ChatResponse
from app.auth.privy_sessions import bearer_token as _bearer
from app.auth.rate_limit import check_rate_limit
from app.config import settings
from app.games.scores import get_session_identity

router = APIRouter(prefix="/aria/holding/member", tags=["aria-holding-member"])


async def _require_member(authorization: str | None) -> tuple[str, str]:
    identity = await get_session_identity(_bearer(authorization))
    if not identity:
        raise HTTPException(status_code=401, detail="Member session required.")
    return identity


class MemberTouchBody(BaseModel):
    site_slug: str = Field(default="kikou", min_length=1, max_length=32)
    game_id: str | None = Field(default="2048", max_length=32)


class MemberTouchResponse(BaseModel):
    greeting: str
    visit_count: int
    handle: str
    is_returning: bool
    game_score: int | None = None
    recent_messages: list[dict] = Field(default_factory=list)


class MemberChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    site_slug: str = Field(default="kikou", max_length=32)


class MemberRememberBody(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., min_length=1, max_length=500)


class MemberProfileResponse(BaseModel):
    handle: str
    visit_count: int
    first_seen: str
    last_seen: str
    facts: dict
    game_score: int | None = None
    recent_messages: list[dict] = Field(default_factory=list)


@router.post("/touch", response_model=MemberTouchResponse)
async def member_touch(body: MemberTouchBody, authorization: str | None = Header(None)):
    privy_did, handle = await _require_member(authorization)
    profile = await touch_member(privy_did=privy_did, handle=handle, site_slug=body.site_slug)
    vid = member_visitor_id(privy_did)
    greeting = await build_member_greeting(
        profile, site_slug=body.site_slug, game_id=body.game_id,
    )
    await repertoire_db.save_message("agent", greeting, visitor_id=vid)

    game_score = None
    if body.game_id:
        from app.games.scores import get_score

        game_score = await get_score(
            privy_did=privy_did, site_slug=body.site_slug, game_id=body.game_id,
        )

    messages = await repertoire_db.get_messages(limit=6, visitor_id=vid)
    return MemberTouchResponse(
        greeting=greeting,
        visit_count=profile.visit_count,
        handle=profile.handle,
        is_returning=profile.is_returning,
        game_score=game_score,
        recent_messages=messages,
    )


@router.get("/profile", response_model=MemberProfileResponse)
async def member_profile(
    authorization: str | None = Header(None),
    site_slug: str = "kikou",
    game_id: str = "2048",
):
    privy_did, _ = await _require_member(authorization)
    profile = await get_member_profile(privy_did)
    if not profile:
        raise HTTPException(status_code=404, detail="No member profile yet — call /touch first.")

    from app.games.scores import get_score

    game_score = await get_score(privy_did=privy_did, site_slug=site_slug, game_id=game_id)
    vid = member_visitor_id(privy_did)
    messages = await repertoire_db.get_messages(limit=8, visitor_id=vid)
    return MemberProfileResponse(
        handle=profile.handle,
        visit_count=profile.visit_count,
        first_seen=profile.first_seen,
        last_seen=profile.last_seen,
        facts=profile.facts,
        game_score=game_score,
        recent_messages=messages,
    )


@router.post("/chat", response_model=ChatResponse)
async def member_chat(body: MemberChatBody, authorization: str | None = Header(None)):
    privy_did, _ = await _require_member(authorization)
    vid = member_visitor_id(privy_did)

    allowed = check_rate_limit(
        f"aria_member_chat:{privy_did}",
        max_attempts=settings.aria_chat_rate_limit_per_hour,
        window_seconds=3600,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")

    return await aria_brain.process(
        body.message.strip(),
        lang=LANG_FR,
        visitor_id=vid,
        public_mode=True,
    )


@router.post("/remember")
async def member_remember(body: MemberRememberBody, authorization: str | None = Header(None)):
    privy_did, _ = await _require_member(authorization)
    await remember_fact(privy_did, body.key, body.value)
    return {"ok": True, "key": body.key}