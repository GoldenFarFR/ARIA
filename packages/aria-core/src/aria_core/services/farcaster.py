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
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://api.warpcast.com/v2/user-by-username"
_USER_BY_FID_URL = "https://api.warpcast.com/v2/user"
# 09/08, real bug found live (backtest against 8 real Base pump tokens):
# Warpcast migrated its public-facing domain to farcaster.xyz, but this
# regex only ever recognized the old warpcast.com/<username> form -- a
# project declaring a farcaster.xyz/<username> profile link (CLANKER's real
# case) silently fell through to "unreadable Farcaster URL", never even
# reaching the API call. [\w.\-]+ naturally does NOT match a channel URL
# (farcaster.xyz/~/channel/<name>, DEGEN's real case) -- the leading "~" is
# outside that character class, so a channel link still correctly yields no
# match rather than a wrong username lookup; verifying a CHANNEL (not a
# user profile) would need separate logic, not attempted here.
_USERNAME_RE = re.compile(r"(?:warpcast\.com|farcaster\.xyz)/([\w.\-]+)", re.IGNORECASE)
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


@dataclass(frozen=True)
class FarcasterProfile:
    """Richer profile than FarcasterProfileVerification -- built for the
    07/24 reverse-lookup need (a name/ENS/linked X account for a wallet
    address), not the 07/19 "verify a declared link" use case above.
    ``x_username`` comes from Warpcast's own ``connectedAccounts`` field
    (platform == "x") -- confirmed live (07/24) on a real profile (fid=3):
    Warpcast exposes the linked X account for FREE, no Neynar/x402 payment
    needed for this specific signal."""

    available: bool
    exists: bool | None = None
    fid: int | None = None
    username: str | None = None
    display_name: str | None = None
    follower_count: int | None = None
    spam_label: str | None = None
    x_username: str | None = None
    eth_wallets: list[str] = field(default_factory=list)
    error: str | None = None


async def get_profile_by_fid(fid: int) -> FarcasterProfile:
    """Resolves a Farcaster fid to a full profile via the same free, no-key
    Warpcast API as verify_profile() above -- confirmed live (07/24):
    `api.warpcast.com/v2/user?fid=<fid>` returns HTTP 200 with no auth."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            res = await client.get(_USER_BY_FID_URL, params={"fid": fid})
    except Exception as exc:  # noqa: BLE001
        logger.info("farcaster: get_profile_by_fid failed for fid=%s (%s)", fid, exc)
        return FarcasterProfile(available=False, error=f"request failed ({exc})")

    if res.status_code == 404:
        return FarcasterProfile(available=True, exists=False)
    if res.status_code != 200:
        return FarcasterProfile(available=False, error=f"HTTP {res.status_code}")

    try:
        data = res.json()
    except Exception as exc:  # noqa: BLE001
        return FarcasterProfile(available=False, error=f"unreadable response ({exc})")

    user = (data.get("result") or {}).get("user") or {}
    if not user:
        return FarcasterProfile(available=True, exists=False)

    extras = user.get("extras") or {}
    connected = user.get("connectedAccounts") or []
    x_username = None
    for account in connected:
        if isinstance(account, dict) and account.get("platform") == "x" and not account.get("expired"):
            x_username = account.get("username")
            break

    eth_wallets = extras.get("ethWallets")
    return FarcasterProfile(
        available=True, exists=True,
        fid=user.get("fid"),
        username=user.get("username"),
        display_name=user.get("displayName"),
        follower_count=user.get("followerCount"),
        spam_label=extras.get("publicSpamLabel"),
        x_username=x_username,
        eth_wallets=[w for w in eth_wallets if isinstance(w, str)] if isinstance(eth_wallets, list) else [],
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
