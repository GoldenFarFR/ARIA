from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.games.pot import get_pot_status, register_deposit
from app.games.scores import get_session_identity

router = APIRouter(prefix="/games/pot", tags=["games-pot"])


class PotStatusResponse(BaseModel):
    round_id: str
    pot_usdc: str
    entries: int
    ends_at: str
    user_entered: bool
    deposit_usdc: str


class PotDepositBody(BaseModel):
    wallet: str = Field(min_length=42, max_length=42)
    tx_hash: str = Field(min_length=66, max_length=66)


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


@router.get("/{site_slug}/current", response_model=PotStatusResponse)
async def pot_current(
    site_slug: str,
    wallet: str | None = Query(default=None),
):
    return await get_pot_status(site_slug=site_slug, wallet=wallet)


@router.post("/{site_slug}/deposit", response_model=PotStatusResponse)
async def pot_deposit(
    site_slug: str,
    body: PotDepositBody,
    authorization: str | None = Header(None),
):
    token = _bearer(authorization)
    identity = await get_session_identity(token)
    privy_did = identity[0] if identity else None

    try:
        return await register_deposit(
            site_slug=site_slug,
            wallet=body.wallet,
            tx_hash=body.tx_hash,
            privy_did=privy_did,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc