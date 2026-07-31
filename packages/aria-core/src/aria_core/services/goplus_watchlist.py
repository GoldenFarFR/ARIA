"""GoPlus honeypot watchlist -- Item #212 (29/07).

Root cause found under real conditions: GoPlus's Free tier (confirmed live on
the operator's dashboard, gopluslabs.io/dashboard) caps at **150,000 CU/month
AND 30,000 CU/day** -- the throttle calibrated on 21/07 (6.667s, 9 req/min)
only respected the CU/min ceiling (150), which turns out to be the LEAST
restrictive of the three. A single isolated call (circuit breaker closed, no
concurrent load, WETH contract -- never itself suspect) still failed with
"rate limit" immediately after a full cooldown, proving the real bottleneck
is the cumulative monthly/daily budget, not the instantaneous rate.

Sustainable rate to never exhaust the monthly budget even running 24/7 (90%
margin, same doctrine as the rest of this project):
    135,000 CU/month (90% of 150,000) / 15 CU per EVM token
    / 43,200 min/month (30 days) = ~288s between honeypot checks.

Operator-designed fix (29/07): instead of a synchronous honeypot call on
every candidate that reaches the gate (today's behavior, which starves the
instant a fresh candidate needs checking), maintain a WATCHLIST of up to
``MAX_WATCHLIST_SIZE`` already-free-gate-qualified candidates, refreshed
in the background at the sustainable rate. A candidate already in the
watchlist with a status younger than ``WATCHLIST_FRESHNESS_HOURS`` (48h) never
triggers a new network call -- ``momentum_entry._check_honeypot`` reads it
directly, free and instant.

31/07 -- ``MAX_WATCHLIST_SIZE`` raised 600 -> 2000 (explicit operator decision,
"on fera le tri au fur et a mesure", same day as widening swing's discovery
funnel -- R/R floor removed, liquidity floor lowered -- both feed more
candidates into this same shared pool). The 600/~288s-per-token math above
(GoPlus as sole source) is now HISTORICAL: ``run_goplus_watchlist_cycle``
(momentum_entry.py, Item #212 follow-up, 29/07) reworked Honeypot.is into the
PRIMARY source, batched 100/passage at a ~5min heartbeat cadence -- draining
2000 slots takes ~20 passages, ~1h40 for a full refresh cycle, comfortably
inside ``WATCHLIST_FRESHNESS_HOURS`` (48h). GoPlus itself is now only a
last-resort fallback (capped at 1 call/passage) when Honeypot.is fails.

This is what actually preserves the "ARIA must be first" speed doctrine: the
background cycle keeps the KNOWN universe's honeypot status warm continuously
so that BY THE TIME a technical buy signal fires, the check is already done
-- the only cost falls on a genuinely brand-new candidate never seen before,
which now gets queued (HOLD, retried later) instead of failing on a rate
limit that would have rejected it anyway under the old synchronous path.

Priority score (``compute_priority_score``) is deliberately computed WITHOUT
any GoPlus data -- it exists to decide who gets a scarce slot BEFORE the
honeypot check runs, so it can only use signals already free at that point
in the pipeline (liquidity, 24h volume) -- same spirit as
``candidate_ranking.py``'s liquidity scoring, not the full
``dex_composite_score.py`` (whose main pillar needs ``security``, i.e. the
very call this watchlist exists to defer)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from math import log10

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services.goplus import TokenSecurity

logger = logging.getLogger(__name__)

# 31/07 -- raised 600 -> 2000 (explicit operator decision). See the module
# docstring above for why the old 288s/token GoPlus-only math no longer
# applies (Honeypot.is is now the primary, much faster source).
MAX_WATCHLIST_SIZE = 2000

# A watchlist entry checked more recently than this is used as-is, no network
# call. Slightly more than one full cycle (48h) would be self-defeating (a
# slot never refreshes before its own neighbors) -- kept at exactly the cycle
# length: worst case, an entry is re-checked right as it goes stale.
WATCHLIST_FRESHNESS_HOURS = 48.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(contract: str, chain: str) -> tuple[str, str]:
    chain = (chain or "").strip().lower()
    contract = (contract or "").strip()
    if chain != "solana":
        contract = contract.lower()
    return contract, chain


async def _ensure_table() -> None:
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS goplus_watchlist (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                priority_score REAL NOT NULL DEFAULT 0.0,
                added_at TEXT NOT NULL,
                last_checked_at TEXT,
                security_json TEXT,
                checked_available INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


def compute_priority_score(liquidity_usd: float | None, volume_24h_usd: float | None) -> float:
    """Priority to enter the 600 slots -- liquidity (log scale) + real
    activity (volume/liquidity ratio), the only signals free at this stage
    of the pipeline (both already on the ``PairSnapshot`` fetched for the
    hard gates, zero extra network cost). NOT a security score -- the
    honeypot check itself remains the only judge of that, deferred here."""
    liq = max(0.0, liquidity_usd or 0.0)
    vol = max(0.0, volume_24h_usd or 0.0)
    liq_score = min(70.0, 15.0 * log10(liq / 10_000.0)) if liq >= 10_000.0 else 0.0
    activity_ratio = (vol / liq) if liq > 0 else 0.0
    activity_score = min(30.0, activity_ratio * 30.0)
    return round(liq_score + activity_score, 2)


async def add_or_touch(contract: str, chain: str, priority_score: float) -> bool:
    """Adds a new candidate, or refreshes its priority score if already
    present. Returns True if the candidate has (or keeps) a slot, False if
    the watchlist is full (600) and this candidate's score doesn't beat the
    current worst entry -- in which case nothing is written (no partial
    state, no eviction attempted for nothing)."""
    await _ensure_table()
    contract, chain = _normalize(contract, chain)
    if not contract or not chain:
        return False

    async with aiosqlite.connect(str(aria_db_path())) as db:
        existing = await (
            await db.execute(
                "SELECT 1 FROM goplus_watchlist WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
        if existing is not None:
            await db.execute(
                "UPDATE goplus_watchlist SET priority_score = ? WHERE contract = ? AND chain = ?",
                (priority_score, contract, chain),
            )
            await db.commit()
            return True

        count_row = await (await db.execute("SELECT COUNT(*) FROM goplus_watchlist")).fetchone()
        count = int(count_row[0]) if count_row else 0

        if count < MAX_WATCHLIST_SIZE:
            await db.execute(
                "INSERT INTO goplus_watchlist (contract, chain, priority_score, added_at) "
                "VALUES (?, ?, ?, ?)",
                (contract, chain, priority_score, _now_iso()),
            )
            await db.commit()
            return True

        worst = await (
            await db.execute(
                "SELECT contract, chain, priority_score FROM goplus_watchlist "
                "ORDER BY priority_score ASC LIMIT 1"
            )
        ).fetchone()
        if worst is None or priority_score <= worst[2]:
            return False

        await db.execute(
            "DELETE FROM goplus_watchlist WHERE contract = ? AND chain = ?",
            (worst[0], worst[1]),
        )
        await db.execute(
            "INSERT INTO goplus_watchlist (contract, chain, priority_score, added_at) "
            "VALUES (?, ?, ?, ?)",
            (contract, chain, priority_score, _now_iso()),
        )
        await db.commit()
        logger.info(
            "goplus_watchlist: evicted %s/%s (score %.1f) for %s/%s (score %.1f)",
            worst[0], worst[1], worst[2], contract, chain, priority_score,
        )
        return True


async def get_fresh(contract: str, chain: str, *, max_age_hours: float = WATCHLIST_FRESHNESS_HOURS) -> TokenSecurity | None:
    """Returns the last known ``TokenSecurity`` if checked within
    ``max_age_hours``, else None (never checked yet, or stale -- both treated
    identically by the caller: no free answer available, fall back to
    queuing this candidate)."""
    await _ensure_table()
    contract, chain = _normalize(contract, chain)
    async with aiosqlite.connect(str(aria_db_path())) as db:
        row = await (
            await db.execute(
                "SELECT last_checked_at, security_json FROM goplus_watchlist "
                "WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    try:
        checked_at = datetime.fromisoformat(row[0])
    except ValueError:
        return None
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - checked_at > timedelta(hours=max_age_hours):
        return None
    try:
        return TokenSecurity(**json.loads(row[1]))
    except (ValueError, TypeError):
        return None


async def next_due(limit: int = 1) -> list[dict]:
    """Candidates most in need of a refresh for the background cycle --
    never-checked entries first (NULL sorts first via the boolean ordering
    below), then oldest-checked first (round-robin)."""
    await _ensure_table()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM goplus_watchlist "
                "ORDER BY (last_checked_at IS NULL) DESC, last_checked_at ASC "
                "LIMIT ?",
                (max(0, limit),),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def record_result(contract: str, chain: str, security: TokenSecurity) -> None:
    """Persists the freshly-fetched security status. Best-effort: a failed
    GoPlus call (``available=False``) is still recorded (with
    ``checked_available=0``) so the round-robin moves on to the next
    candidate rather than retrying the same one immediately."""
    await _ensure_table()
    contract, chain = _normalize(contract, chain)
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "UPDATE goplus_watchlist SET last_checked_at = ?, security_json = ?, "
            "checked_available = ? WHERE contract = ? AND chain = ?",
            (_now_iso(), json.dumps(asdict(security)), 1 if security.available else 0, contract, chain),
        )
        await db.commit()


async def remove(contract: str, chain: str) -> None:
    """Drops a candidate (e.g. confirmed honeypot -> transferred to
    ``momentum_blacklist`` instead, no longer needs a watchlist slot)."""
    await _ensure_table()
    contract, chain = _normalize(contract, chain)
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "DELETE FROM goplus_watchlist WHERE contract = ? AND chain = ?", (contract, chain),
        )
        await db.commit()


async def count() -> int:
    await _ensure_table()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM goplus_watchlist")).fetchone()
    return int(row[0]) if row else 0


async def list_all() -> list[dict]:
    """Full dump (operator diagnostic / Telegram command) -- best score first."""
    await _ensure_table()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM goplus_watchlist ORDER BY priority_score DESC")
        ).fetchall()
    return [dict(r) for r in rows]


async def format_status_report(top_n: int = 20) -> str:
    """Text diagnostic (Telegram ``/goplusqueue``) -- a full 600-row dump
    would blow past Telegram's message length, so this shows the total count,
    the per-chain breakdown, and only the top ``top_n`` by priority score."""
    rows = await list_all()
    if not rows:
        return (
            "📋 Watchlist honeypot GoPlus — vide pour l'instant.\n"
            "Se peuple au fil du scan momentum réel (candidats déjà passés par "
            "tous les gates gratuits) — rien à voir tant que le pipeline n'a pas "
            "encore tourné avec ce gate actif."
        )

    by_chain: dict[str, int] = {}
    never_checked = 0
    for r in rows:
        by_chain[r["chain"]] = by_chain.get(r["chain"], 0) + 1
        if not r["last_checked_at"]:
            never_checked += 1

    lines = [
        f"📋 Watchlist honeypot GoPlus — {len(rows)}/{MAX_WATCHLIST_SIZE} slots.",
        f"Jamais vérifiés : {never_checked}",
        "Par chaîne : " + ", ".join(f"{c}={n}" for c, n in sorted(by_chain.items())),
        "",
        f"Top {min(top_n, len(rows))} par score de priorité :",
    ]
    for r in rows[:top_n]:
        checked = r["last_checked_at"] or "jamais"
        available = "clair" if r["checked_available"] else ("indisponible" if r["last_checked_at"] else "en attente")
        lines.append(
            f"- {r['contract'][:8]}…{r['contract'][-4:]} ({r['chain']}) "
            f"score={r['priority_score']:.1f} · {available} · dernier check {checked}"
        )
    return "\n".join(lines)
