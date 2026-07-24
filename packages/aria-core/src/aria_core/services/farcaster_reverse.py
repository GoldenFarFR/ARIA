"""Reverse-lookup a wallet address to a Farcaster identity (07/24, direct
answer to an operator question: "can we get a name/ENS/linked X account for
this wallet?"). Orchestrates two already-free/already-paid clients, zero new
cost:

1. ``dune.get_farcaster_fid_by_address`` -- address -> fid, via the already
   configured/calibrated Dune client (no new billing surface).
2. ``farcaster.get_profile_by_fid`` -- fid -> full profile (username, ENS-like
   display name, follower count, linked X account), via the free/no-key
   Warpcast API.

Deliberately NOT a new paid integration: Neynar was retested the same
evening it first required an x402 payment (services/farcaster.py's own
header comment) and rejected as unnecessary once Warpcast's own
``connectedAccounts`` field was confirmed (07/24, live call) to already
expose a linked X account for free -- this module is the "build in-house"
alternative the operator explicitly asked for, not a workaround for a
missing paid API.

Dome doctrine, same as every other reverse-lookup in this project: no
verification found is a NORMAL, common outcome (most wallets never verified
a Farcaster account), never treated as an error.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FarcasterIdentity:
    available: bool = True
    found: bool = False
    fid: int | None = None
    username: str | None = None
    display_name: str | None = None
    follower_count: int | None = None
    x_username: str | None = None
    eth_wallets: list[str] = field(default_factory=list)
    error: str | None = None


async def reverse_lookup_address(address: str) -> FarcasterIdentity:
    """Full address -> Farcaster identity pipeline. Never raises -- any
    failure at either step degrades to ``available=False`` with the real
    reason, never a fabricated identity."""
    from aria_core.services import dune, farcaster

    fid_result = await dune.get_farcaster_fid_by_address(address)
    if not fid_result.available:
        return FarcasterIdentity(available=False, error=fid_result.error)
    if fid_result.fid is None:
        return FarcasterIdentity(available=True, found=False)

    profile = await farcaster.get_profile_by_fid(fid_result.fid)
    if not profile.available:
        return FarcasterIdentity(available=False, error=profile.error)
    if not profile.exists:
        return FarcasterIdentity(available=True, found=False, fid=fid_result.fid)

    return FarcasterIdentity(
        available=True, found=True,
        fid=profile.fid,
        username=profile.username,
        display_name=profile.display_name,
        follower_count=profile.follower_count,
        x_username=profile.x_username,
        eth_wallets=profile.eth_wallets,
    )


def format_identity(identity: FarcasterIdentity) -> str:
    """Telegram-friendly rendering for /walletscore-style enrichment."""
    if not identity.available:
        return f"Farcaster : indisponible ({identity.error or 'raison inconnue'})"
    if not identity.found:
        return "Farcaster : aucun compte vérifié pour cette adresse"
    parts = [f"@{identity.username}" if identity.username else f"fid {identity.fid}"]
    if identity.display_name:
        parts.append(identity.display_name)
    if identity.x_username:
        parts.append(f"X: @{identity.x_username}")
    if identity.follower_count is not None:
        parts.append(f"{identity.follower_count} abonnés")
    return "Farcaster : " + " · ".join(parts)
