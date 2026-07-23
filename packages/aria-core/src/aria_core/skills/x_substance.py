"""X "substance" signal -- real credibility of an X account (23/07, enriched
the same day after finding a full-profile provider).

Diligence history (project norm: verify before coding):
- Twit.sh (x402, already in prod, reserved for ``conviction_research.py`` for
  the publication cadence) only returns PER-TWEET metrics, never a profile
  (no followers/following/join date).
- Tavily ``extract`` on the profile page (verified for real on @crynuxio)
  renders the JS and exposes "Joined October 2023" -- but NOT the
  followers/following counts (absent from the extracted text, confirmed via
  grep). Publication regularity via Tavily was also EVALUATED then DROPPED:
  the timestamped status links from an extracted profile page do NOT reflect
  the real recent chronological feed ("highlights" tweets, the most engaged
  ones, tested twice -- extract_depth basic AND advanced, same result).
- **TwitterAPI.io** (``services/twitterapi_io.py``, diligenced on 23/07:
  ScamAdviser "legit and safe", positive Trustpilot, official MCP skill,
  0,18$/1000 profiles) fills the profile gap: a single call gives
  followers/following/creation date, verified in real conditions on
  @crynuxio (3676 followers, 242 following, created 27/10/2023).
- **Activity/engagement** (same day, explicit operator request after a
  table confirming that twit.sh ALSO provides them): reusing twit.sh here
  would duplicate a paid call already made by ``conviction_research.py`` on
  the SAME account -- TwitterAPI.io has an equivalent dedicated endpoint
  (``/twitter/user/last_tweets``, verified for real: date + engagement per
  tweet), zero new provider, zero coupling between the two modules.
- **Xquik** (native x402 provider listed in awesome-x402) evaluated and
  DROPPED: its payment challenge points to the "Tempo" network (Stripe/
  Paradigm, chainId 4217), NOT Base/USDC -- incompatible with the existing
  CDP infra, disproportionate effort for this advisory-only signal.

Cascading architecture: TwitterAPI.io is tried FIRST (if
``TWITTERAPI_IO_KEY`` is configured) -- profile THEN latest tweets
(best-effort, a failure on the tweets alone only degrades these 2 axes,
never the whole signal). If the profile is absent/unavailable, fall back to
Tavily ``extract`` (account age ONLY, as before this fix) -- graceful
degradation, never blocking."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_JOIN_DATE_RE = re.compile(r"Joined\s+([A-Za-z]+\s+\d{4})")

# Following/followers ratio thresholds -- taken from the external formula
# proposed by the operator (independent of the axes already dropped for lack
# of data, so reusable as-is now that the ratio is actually measurable).
_RATIO_EXCELLENT_MAX = 1.0
_RATIO_GOOD_MAX = 1.5
_RATIO_ACCEPTABLE_MAX = 3.0

# Regularity window -- same spirit as the initial design conceived for
# Tavily (never used, the source was unreliable); reusable now that
# TwitterAPI.io gives a real dated chronological feed.
_REGULARITY_LOOKBACK_DAYS = 90
_REGULARITY_LOOKBACK_WEEKS = _REGULARITY_LOOKBACK_DAYS // 7
_MIN_ACTIVE_WEEKS_FOR_FULL_SCORE = 8  # out of 12 weeks

# Normalized engagement thresholds (average (likes+replies+retweets+quotes)/tweet
# / followers) -- rough calibration, not derived from an external study, to
# adjust with more real data over time (cf. observed CNX case:
# ~1,3-1,9% on an account already judged positive elsewhere).
_ENGAGEMENT_EXCELLENT_MIN = 0.01
_ENGAGEMENT_GOOD_MIN = 0.003
_ENGAGEMENT_WEAK_MIN = 0.0005


@dataclass
class XSubstanceFacts:
    available: bool = False
    error: str | None = None
    account_age_days: int | None = None
    followers: int | None = None
    following: int | None = None
    active_weeks_recent: int | None = None
    tweets_analyzed: int = 0
    avg_engagement_rate: float | None = None
    source: str = "none"  # "twitterapi_io" | "tavily_fallback"


@dataclass
class XSubstanceVerdict:
    signal: str  # "positive" | "neutral" | "weak" | "unknown"
    score: float | None
    points: list[str] = field(default_factory=list)


def _parse_join_date(text: str, *, now: datetime) -> datetime | None:
    m = _JOIN_DATE_RE.search(text or "")
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1).strip(), "%B %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt if dt <= now else None


async def _default_twitterapi_fetch(handle: str):
    from aria_core.services.twitterapi_io import fetch_user_profile

    return await fetch_user_profile(handle)


async def _default_twitterapi_tweets(handle: str):
    from aria_core.services.twitterapi_io import fetch_last_tweets

    return await fetch_last_tweets(handle)


async def _default_extract(handle: str):
    from aria_core.services.tavily import tavily_client

    return await tavily_client.extract([f"https://x.com/{handle}"], caller="x_substance")


def _activity_from_tweets(tweets: list, *, followers: int, now: datetime) -> tuple[int, float | None]:
    """(recent active weeks, average engagement rate normalized by
    followers). ``None`` for the rate if followers<=0 (never a division
    by zero nor a fabricated number)."""
    cutoff = now - timedelta(days=_REGULARITY_LOOKBACK_DAYS)
    recent_weeks = {(now - t.created_at).days // 7 for t in tweets if cutoff <= t.created_at <= now}

    if followers <= 0 or not tweets:
        return len(recent_weeks), None

    total_engagement = sum(t.like_count + t.reply_count + t.retweet_count + t.quote_count for t in tweets)
    avg_rate = (total_engagement / len(tweets)) / followers
    return len(recent_weeks), avg_rate


async def gather_x_substance_facts(
    x_handle: str | None, *, twitterapi_fn=None, tweets_fn=None, extract_fn=None, now: datetime | None = None,
) -> XSubstanceFacts:
    """Best-effort collection, never blocking. Injectable functions for
    tests (same pattern as ``fetch=`` in ``github_substance.py``).
    TwitterAPI.io tried FIRST (profile then tweets), Tavily as a fallback
    (age only, never activity/engagement without follower counts)."""
    handle = (x_handle or "").lstrip("@").strip()
    if not handle:
        return XSubstanceFacts(available=False, error="missing X handle")

    now = now or datetime.now(timezone.utc)

    twitterapi_fn = twitterapi_fn or _default_twitterapi_fetch
    try:
        profile = await twitterapi_fn(handle)
    except Exception:  # noqa: BLE001 -- never blocking
        profile = None

    if profile is not None:
        active_weeks: int | None = None
        avg_engagement: float | None = None
        tweets_analyzed = 0

        tweets_fn_ = tweets_fn or _default_twitterapi_tweets
        try:
            tweets = await tweets_fn_(handle)
        except Exception:  # noqa: BLE001 -- degrades only these 2 axes, never the whole signal
            tweets = None

        if tweets:
            tweets_analyzed = len(tweets)
            active_weeks, avg_engagement = _activity_from_tweets(
                tweets, followers=profile.followers, now=now,
            )

        return XSubstanceFacts(
            available=True,
            account_age_days=(now - profile.created_at).days,
            followers=profile.followers,
            following=profile.following,
            active_weeks_recent=active_weeks,
            tweets_analyzed=tweets_analyzed,
            avg_engagement_rate=avg_engagement,
            source="twitterapi_io",
        )

    extract_fn = extract_fn or _default_extract
    try:
        result = await extract_fn(handle)
    except Exception as exc:  # noqa: BLE001 -- never blocking
        return XSubstanceFacts(available=False, error=str(exc))

    if not result.available or not result.pages:
        return XSubstanceFacts(available=False, error=result.error or "profile not found")

    text = result.pages[0].raw_content
    join_date = _parse_join_date(text, now=now)

    if join_date is None:
        return XSubstanceFacts(available=False, error="join date not found on profile")

    return XSubstanceFacts(
        available=True, account_age_days=(now - join_date).days, source="tavily_fallback",
    )


def _account_age_score(account_age_days: int) -> float:
    months = account_age_days / 30.0
    if months >= 18:
        return 100.0
    if months >= 12:
        return 70.0
    if months >= 6:
        return 40.0
    return 0.0


def _ratio_score(followers: int, following: int) -> float:
    if followers <= 0:
        return 0.0
    ratio = following / followers
    if ratio <= _RATIO_EXCELLENT_MAX:
        return 100.0
    if ratio <= _RATIO_GOOD_MAX:
        return 70.0
    if ratio <= _RATIO_ACCEPTABLE_MAX:
        return 30.0
    return 0.0


def _regularity_score(active_weeks: int) -> float:
    return min(1.0, active_weeks / _MIN_ACTIVE_WEEKS_FOR_FULL_SCORE) * 100.0


def _engagement_score(rate: float) -> float:
    if rate >= _ENGAGEMENT_EXCELLENT_MIN:
        return 100.0
    if rate >= _ENGAGEMENT_GOOD_MIN:
        return 70.0
    if rate >= _ENGAGEMENT_WEAK_MIN:
        return 40.0
    return 0.0


def judge_x_substance(facts: XSubstanceFacts) -> XSubstanceVerdict:
    """Pure judgment, no network call. Up to 4 criteria (age, ratio,
    regularity, engagement) when TwitterAPI.io provided everything; degrades
    honestly (weights redistributed) based on what is actually missing --
    never a fabricated axis. Network/thematic alignment from the external
    proposal remain out of scope (semantic analysis of the real feed)."""
    if not facts.available or facts.account_age_days is None:
        return XSubstanceVerdict(signal="unknown", score=None, points=[facts.error or "unavailable"])

    age_score = _account_age_score(facts.account_age_days)
    months = int(facts.account_age_days / 30.0)

    has_ratio = facts.followers is not None and facts.following is not None
    has_regularity = facts.active_weeks_recent is not None
    has_engagement = facts.avg_engagement_rate is not None

    weighted = [(0.30 if (has_ratio or has_regularity or has_engagement) else 1.0, age_score)]
    detail_parts = [f"account aged {months} months"]

    if has_ratio:
        ratio_score = _ratio_score(facts.followers, facts.following)
        weighted.append((0.25, ratio_score))
        ratio_txt = f"{facts.following}/{facts.followers}" if facts.followers else "n/a"
        detail_parts.append(f"{facts.followers} followers / {facts.following} following (ratio {ratio_txt})")

    if has_regularity:
        regularity_score = _regularity_score(facts.active_weeks_recent)
        weighted.append((0.25, regularity_score))
        detail_parts.append(
            f"active {facts.active_weeks_recent}/{_REGULARITY_LOOKBACK_WEEKS} recent weeks"
        )

    if has_engagement:
        engagement_score = _engagement_score(facts.avg_engagement_rate)
        weighted.append((0.20, engagement_score))
        detail_parts.append(f"average engagement {facts.avg_engagement_rate * 100:.2f}% of followers/tweet")

    total_weight = sum(w for w, _ in weighted)
    score = sum(w * s for w, s in weighted) / total_weight

    if not has_ratio and not has_regularity and not has_engagement:
        detail_parts.append("followers/following/activity unavailable, falling back to age only")

    points = [f"substance {score:.1f}/100 -- " + ", ".join(detail_parts)]

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"

    return XSubstanceVerdict(signal=signal, score=round(score, 1), points=points)
