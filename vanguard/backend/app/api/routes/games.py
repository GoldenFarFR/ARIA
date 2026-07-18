from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.privy_sessions import bearer_token as _bearer
from app.games.scores import get_score, get_session_identity, leaderboard, upsert_score

router = APIRouter(prefix="/games/scores", tags=["games"])


class ScoreUpsertBody(BaseModel):
    score: int = Field(ge=0, le=10_000_000)
    better: str = Field(default="max", pattern="^(max|min)$")


class ScoreMeResponse(BaseModel):
    score: int | None
    game_id: str
    site_slug: str


class LeaderboardEntry(BaseModel):
    score: int
    handle: str
    updated_at: str


async def _require_identity(authorization: str | None) -> tuple[str, str]:
    identity = await get_session_identity(_bearer(authorization))
    if not identity:
        raise HTTPException(status_code=401, detail="Member session required for game scores.")
    return identity


@router.get("/{site_slug}/{game_id}/me", response_model=ScoreMeResponse)
async def score_me(site_slug: str, game_id: str, authorization: str | None = Header(None)):
    privy_did, _ = await _require_identity(authorization)
    score = await get_score(privy_did=privy_did, site_slug=site_slug, game_id=game_id)
    return ScoreMeResponse(score=score, game_id=game_id, site_slug=site_slug)


@router.put("/{site_slug}/{game_id}")
async def score_upsert(
    site_slug: str,
    game_id: str,
    body: ScoreUpsertBody,
    authorization: str | None = Header(None),
):
    privy_did, _ = await _require_identity(authorization)
    stored = await upsert_score(
        privy_did=privy_did,
        site_slug=site_slug,
        game_id=game_id,
        score=body.score,
        better=body.better,
    )
    return {"score": stored, "game_id": game_id, "site_slug": site_slug}


@router.get("/{site_slug}/{game_id}/leaderboard", response_model=list[LeaderboardEntry])
async def score_leaderboard(
    site_slug: str,
    game_id: str,
    limit: int = Query(default=10, ge=1, le=50),
):
    rows = await leaderboard(site_slug=site_slug, game_id=game_id, limit=limit)
    return [LeaderboardEntry(**row) for row in rows]