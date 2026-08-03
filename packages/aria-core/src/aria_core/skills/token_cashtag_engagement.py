"""Token-cashtag engagement signal (03/08, real due-diligence session, C-MEM
case). A credible dev plus a credible product is NOT sufficient to conclude
a TOKEN is being actively maintained -- the C-MEM case showed a fully
legitimate, actively-developed open-source product (89K+ GitHub stars,
daily commits) sitting on a token whose own cashtag hadn't been mentioned by
the project's account in 2.5 months, across three failed relaunches, while
the founder's real business had quietly pivoted to a token-unrelated SaaS.

Neither `github_substance.py` (judges the PRODUCT's development) nor
`x_substance.py` (judges the ACCOUNT's general activity/engagement) answers
this -- both can score highly while the token itself is abandoned. This
signal is orthogonal: it asks "when did this account last talk about THIS
TOKEN specifically" by scanning the account's recent tweets for the token's
own symbol.

Built on `services/twitterapi_io.py::fetch_last_tweets` (already used by
`x_substance.py`, no new client/provider -- the `text` field was added to
`TwitterApiIoTweet` for this exact purpose) -- no separate cashtag-search API
needed. Pure, deterministic JUDGE, same doctrine as the other Substance
signals: produces a weighted signal (positive/neutral/concern/unknown) that
FEEDS ARIA's reasoning -- never an automatic rejection."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Days since the last mention of the token's own symbol, above which the
# absence reads as a real disengagement signal rather than a quiet week.
# Calibrated on the C-MEM case (silence since 18/05, ~75 days at the time of
# that session) -- deliberately well under that, to adjust with more real
# cases.
_STALE_AFTER_DAYS = 30
_WEAK_AFTER_DAYS = 14


def _symbol_pattern(symbol: str) -> re.Pattern[str]:
    # Matches "$SYMBOL" or bare "SYMBOL" as a whole word, case-insensitive --
    # the C-MEM case showed the account using both ("$CMEM", "$MEM") and
    # plain-text mentions ("CMEM is...") interchangeably.
    escaped = re.escape(symbol.lstrip("$"))
    return re.compile(rf"\$?\b{escaped}\b", re.IGNORECASE)


@dataclass(frozen=True)
class TokenCashtagEngagementFacts:
    tweets_scanned: int = 0
    days_since_last_mention: int | None = None  # None = no mention found in the scanned window
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class TokenCashtagEngagementVerdict:
    signal: str  # positive / neutral / concern / unknown
    points: list[str] = field(default_factory=list)


def judge_token_cashtag_engagement(facts: TokenCashtagEngagementFacts) -> TokenCashtagEngagementVerdict:
    """Weighted judgment, never a hard cutoff -- same doctrine as the other
    Substance signals. A missing mention in the scanned window is NOT proof
    of abandonment (the account may simply not have posted about the token
    lately without truly walking away) -- reported as a concern, not a veto."""
    if not facts.available:
        return TokenCashtagEngagementVerdict(
            signal="unknown", points=[facts.error or "activité récente non analysable"],
        )
    if facts.tweets_scanned == 0:
        return TokenCashtagEngagementVerdict(
            signal="unknown", points=["aucun tweet récent trouvé sur ce compte"],
        )
    if facts.days_since_last_mention is None:
        return TokenCashtagEngagementVerdict(
            signal="concern",
            points=[
                f"aucune mention du symbole du token dans les {facts.tweets_scanned} derniers tweets "
                "du compte -- le compte reste actif mais ne parle plus de CE token spécifiquement"
            ],
        )
    days = facts.days_since_last_mention
    if days >= _STALE_AFTER_DAYS:
        return TokenCashtagEngagementVerdict(
            signal="concern",
            points=[f"dernière mention du token il y a {days} jours -- silence prolongé sur le token précisément"],
        )
    if days >= _WEAK_AFTER_DAYS:
        return TokenCashtagEngagementVerdict(
            signal="neutral", points=[f"dernière mention du token il y a {days} jours"],
        )
    return TokenCashtagEngagementVerdict(
        signal="positive", points=[f"mention récente du token il y a {days} jour(s)"],
    )


async def gather_token_cashtag_engagement_facts(
    x_handle: str | None, symbol: str | None, *, tweets_fn=None, now: datetime | None = None,
) -> TokenCashtagEngagementFacts:
    """Best-effort collection, never blocking. Injectable `tweets_fn` for
    tests (same pattern as `x_substance.py`'s `tweets_fn`, default:
    `twitterapi_io.fetch_last_tweets`)."""
    handle = (x_handle or "").lstrip("@").strip()
    sym = (symbol or "").strip()
    if not handle or not sym:
        return TokenCashtagEngagementFacts(available=False, error="handle X ou symbole du token manquant")

    now = now or datetime.now(timezone.utc)

    if tweets_fn is None:
        from aria_core.services.twitterapi_io import fetch_last_tweets as tweets_fn

    try:
        tweets = await tweets_fn(handle)
    except Exception as exc:  # noqa: BLE001 -- never blocking
        return TokenCashtagEngagementFacts(available=False, error=str(exc))

    if not tweets:
        return TokenCashtagEngagementFacts(tweets_scanned=0, available=True)

    pattern = _symbol_pattern(sym)
    matches = [t.created_at for t in tweets if pattern.search(t.text or "")]

    days_since: int | None = None
    if matches:
        most_recent = max(matches)
        days_since = max(0, (now - most_recent).days)

    return TokenCashtagEngagementFacts(
        tweets_scanned=len(tweets), days_since_last_mention=days_since, available=True,
    )
