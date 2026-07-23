"""X (Twitter) social listening — sourcing service for the Radar (Vault 4).

Collects token contract MENTIONS on X and derives a **buzz** signal from
them (how many mentions, how many distinct authors). This buzz is only used
to **source/wake up** candidates: it NEVER decides to buy or sell.

DOME (iron rule): social data is **unreliable** and **hostile by default**
(bot farms, paid shills, fake consensus). It is **sanitized** here (never
interpreted as an instruction), and downstream it's the **on-chain**
analysis that decides (``token_absorber``). Social FILTERS/WAKES UP,
on-chain ARBITRATES.

The network fetch is **injectable** → testable offline. In prod, the default
degrades gracefully (empty list) as long as the X API isn't configured: no
exception, no blocking. Read-only, no signing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# EVM address: 0x + 40 hex. Bounded by non-hex characters to avoid catching
# a prefix of a longer string (e.g. a 64-char hash). Case-insensitive.
_CONTRACT_RE = re.compile(r"(?<![0-9a-fA-Fx])(0x[0-9a-fA-F]{40})(?![0-9a-fA-F])")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HANDLE_MAX = 32


def _sanitize(text: object, max_len: int = 280) -> str:
    """Neutralizes a piece of social data (never an instruction): control chars + angle brackets.

    Same doctrine as ``vc_analysis._sanitize``: strips control characters and
    neutralizes ``<`` ``>`` so a hostile post can't forge a tag and escape an
    untrusted zone downstream.
    """
    s = _CONTROL_CHARS_RE.sub("", str(text or ""))
    s = s.replace("<", "‹").replace(">", "›")
    return s[:max_len]


def extract_contracts(text: str) -> list[str]:
    """Contract addresses (0x + 40 hex) found in a text, lowercased, deduplicated."""
    seen: dict[str, None] = {}
    for m in _CONTRACT_RE.findall(text or ""):
        addr = m.lower()
        seen.setdefault(addr, None)
    return list(seen.keys())


@dataclass(frozen=True)
class SocialSignal:
    """Social buzz around a contract: how many mentions, how many authors.

    This is a SOURCING signal, never a trigger. ``distinct_authors`` filters
    out crude astroturfing (a single author spamming isn't a consensus).
    """

    contract: str
    mentions: int = 0
    distinct_authors: int = 0
    sample_handles: list[str] = field(default_factory=list)


class XSocialClient:
    """X listening client (read-only). ``fetch`` injectable for offline tests.

    In prod, ``fetch`` queries the X/Twitter API (recent search). Not configured →
    defaults to ``_fetch_stub`` which returns ``[]`` (graceful degradation, never blocking).
    """

    def __init__(self, fetch=None) -> None:
        self._fetch = fetch or _fetch_stub

    async def scan_mentions(
        self, query: str = "base token 0x", *, limit: int = 100
    ) -> list[SocialSignal]:
        """Collects posts and aggregates buzz PER mentioned contract.

        ``fetch(query, limit)`` must return a list of posts
        ``{"text": str, "author": str}``. Any unexpected shape is silently
        ignored (never an exception: social data is hostile).
        """
        try:
            posts = await self._fetch(query, limit)
        except Exception as exc:  # noqa: BLE001 — never blocking
            logger.info("x_social: fetch failed (%s) — radar empty this round", exc)
            return []

        agg: dict[str, dict] = {}
        for post in posts or []:
            if not isinstance(post, dict):
                continue
            text = _sanitize(post.get("text", ""))
            author = _sanitize(post.get("author", ""), max_len=_HANDLE_MAX)
            for contract in extract_contracts(text):
                slot = agg.setdefault(
                    contract, {"mentions": 0, "authors": set(), "handles": []}
                )
                slot["mentions"] += 1
                if author:
                    slot["authors"].add(author)
                    if author not in slot["handles"] and len(slot["handles"]) < 5:
                        slot["handles"].append(author)

        signals = [
            SocialSignal(
                contract=contract,
                mentions=slot["mentions"],
                distinct_authors=len(slot["authors"]),
                sample_handles=slot["handles"],
            )
            for contract, slot in agg.items()
        ]
        # Loudest first (mentions then distinct authors).
        signals.sort(key=lambda s: (s.mentions, s.distinct_authors), reverse=True)
        return signals


async def _fetch_stub(query: str, limit: int) -> list[dict]:
    """Offline / not-configured default: no social data (never an error)."""
    logger.info("x_social: no source configured — radar idle (fetch stub)")
    return []


# Convenience singleton (default fetch = stub as long as the X API isn't wired up).
x_social_client = XSocialClient()
