"""Read-only Farcaster client (via the public Warpcast API) -- verifies the
CONTENT of a Farcaster profile declared by a project (07/19, operator feedback).

`api.warpcast.com/v2/user-by-username` (verified live, 07/19) is PUBLIC and
FREE, no key required -- unlike Neynar (retested the same evening: now
requires an x402 payment or an API key, `X-PAYMENT header or API key required`),
Warpcast remains the free path. Provides a real legitimacy signal that Neynar
doesn't expose for free: ``publicSpamLabel`` -- Warpcast's own anti-spam
classification for this account -- in addition to the follower count."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://api.warpcast.com/v2/user-by-username"
_USERNAME_RE = re.compile(r"warpcast\.com/([\w.\-]+)", re.IGNORECASE)
_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class FarcasterProfileVerification:
    available: bool
    exists: bool | None = None
    follower_count: int | None = None
    spam_label: str | None = None
    error: str | None = None


def _parse_username(url: str) -> str | None:
    m = _USERNAME_RE.search(url or "")
    if not m:
        return None
    username = m.group(1).strip("/")
    return username or None


async def verify_profile(url: str) -> FarcasterProfileVerification:
    """Verifies a declared Farcaster profile. Never a bubbling exception."""
    username = _parse_username(url)
    if not username:
        return FarcasterProfileVerification(available=False, error="unreadable Farcaster URL")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            res = await client.get(_API_URL, params={"username": username})
    except Exception as exc:  # noqa: BLE001
        logger.info("farcaster: request failed for %s (%s)", username, exc)
        return FarcasterProfileVerification(available=False, error=f"request failed ({exc})")

    if res.status_code == 404:
        return FarcasterProfileVerification(available=True, exists=False)
    if res.status_code != 200:
        return FarcasterProfileVerification(available=False, error=f"HTTP {res.status_code}")

    try:
        data = res.json()
    except Exception as exc:  # noqa: BLE001
        return FarcasterProfileVerification(available=False, error=f"unreadable response ({exc})")

    user = (data.get("result") or {}).get("user") or {}
    if not user:
        return FarcasterProfileVerification(available=True, exists=False)

    extras = user.get("extras") or {}
    return FarcasterProfileVerification(
        available=True, exists=True,
        follower_count=user.get("followerCount"),
        spam_label=extras.get("publicSpamLabel"),
    )


def format_profile_verification(v: FarcasterProfileVerification) -> str:
    if not v.available:
        return "vérification indisponible"
    if v.exists is False:
        return "profil introuvable (lien mort ou jamais publié -- signal négatif)"
    parts = []
    if v.follower_count is not None:
        parts.append(f"{v.follower_count} abonnés")
    if v.spam_label:
        parts.append(f"label spam Warpcast : {v.spam_label}")
    return ", ".join(parts) if parts else "profil trouvé, détails indisponibles"
