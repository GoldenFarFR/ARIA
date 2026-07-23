"""Copy-trading / bot detection — a wallet that systematically follows
ANOTHER already-scored wallet rather than deciding on its own.

Design validated with the operator (22/07, after independent verification of
an external proposal attributed to "Grok" v2): ``composite_percentile``
(`smart_money.py`) stays PURELY a measure of PERFORMANCE — never mixed with
this signal (Option 1, explicitly chosen). Copy-trading detection is a
SEPARATE flag, purely advisory, never a correction of the composite score
itself (2 wallets with a high score always dominate 10 wallets with a low
score, see `analyze_smart_money`/CLAUDE.md 22/07 — this flag changes nothing
about that).

Mechanics — FREE, no dedicated collection: every wallet scan
(`smart_money._analyze_wallet_multi_token`) already records the timestamp of
its first entry on each token it analyzes to compute the "early entry"
criterion — `record_entry()` is a simple byproduct of this existing
calculation, zero extra network call. A correlation query (self-join on the
table) then detects a wallet that systematically enters within a short
window (5-15 min) AFTER another already-scored wallet, across SEVERAL
distinct tokens — a sign of copying/bot behavior rather than independent
conviction. An isolated overlap on a single token is never suspicious (two
independent wallets can legitimately react to the same public
announcement); the distinct-token threshold filters out this noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Window after another wallet's entry within which an entry is considered a
# possible COPY. Taken as-is from the operator-validated design (22/07) —
# 5 min: any shorter would be indistinguishable from a normal reactive order
# book; 15 min: beyond that, the time correlation weakens too much to remain
# a reliable signal.
_COPY_WINDOW_MIN_SECONDS = 5 * 60
_COPY_WINDOW_MAX_SECONDS = 15 * 60
# Number of DISTINCT tokens on which the pattern must repeat before
# suspecting systematic copy-trading — never on a single token, an isolated
# coincidence isn't a pattern.
_MIN_DISTINCT_TOKENS_FOR_SUSPICION = 3


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_entry_timestamps (
                wallet TEXT NOT NULL,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (wallet, contract, chain)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_wallet_entry_contract_chain "
            "ON wallet_entry_timestamps (contract, chain, entry_ts)"
        )
        await db.commit()


async def record_entry(wallet: str, contract: str, chain: str, entry_ts: datetime) -> None:
    """Records a wallet's first-entry timestamp on a token — idempotent
    (upsert), a wallet/contract/chain pair is never duplicated. Defensive by
    construction: the caller (`smart_money.py`) already swallows every
    exception, but a malformed entry is silently ignored here too rather
    than risking a corrupted write."""
    wallet_l = (wallet or "").strip().lower()
    contract_l = (contract or "").strip().lower()
    chain_l = (chain or "").strip().lower()
    if not wallet_l or not contract_l or not chain_l or entry_ts is None:
        return
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_entry_timestamps (wallet, contract, chain, entry_ts, recorded_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(wallet, contract, chain) DO UPDATE SET entry_ts = excluded.entry_ts, "
            "recorded_at = excluded.recorded_at",
            (wallet_l, contract_l, chain_l, entry_ts.isoformat()),
        )
        await db.commit()


@dataclass(frozen=True)
class CopyTradingFacts:
    distinct_tokens_followed: int = 0
    followed_wallets: list[str] = field(default_factory=list)
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class CopyTradingVerdict:
    flag: str  # copy_trading_suspected / independent / unknown
    points: list[str] = field(default_factory=list)


def judge_copy_trading(facts: CopyTradingFacts) -> CopyTradingVerdict:
    """Pure, deterministic judgment — same doctrine as dev_wallet.py/
    insider_wallets.py: never an automatic rejection, just one more advisory
    flag."""
    if not facts.available:
        return CopyTradingVerdict(flag="unknown", points=[facts.error or "entry history not analyzable"])
    if facts.distinct_tokens_followed < _MIN_DISTINCT_TOKENS_FOR_SUSPICION:
        return CopyTradingVerdict(
            flag="independent",
            points=[f"correlated entries on {facts.distinct_tokens_followed} token(s), below the suspicion threshold"],
        )
    return CopyTradingVerdict(
        flag="copy_trading_suspected",
        points=[
            f"systematically enters {_COPY_WINDOW_MIN_SECONDS // 60}-{_COPY_WINDOW_MAX_SECONDS // 60} min after "
            f"{len(facts.followed_wallets)} already-scored wallet(s), on {facts.distinct_tokens_followed} distinct "
            "tokens -- possible copy-trading/bot rather than independent conviction"
        ],
    )


async def gather_copy_trading_facts(wallet: str, chain: str = "base") -> CopyTradingFacts:
    """Correlates ``wallet``'s entries against those of ALL other wallets
    already recorded on the same chain — a single query (self-join on the
    table), no N+1. Any unavailability -> ``available=False``, never a flag
    inferred from a partial/uncertain correlation."""
    wallet_l = (wallet or "").strip().lower()
    chain_l = (chain or "").strip().lower()
    if not wallet_l or not chain_l:
        return CopyTradingFacts(available=False, error="missing wallet or chain")
    await _ensure_table()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT a.contract AS contract, b.wallet AS followed_wallet
                    FROM wallet_entry_timestamps a
                    JOIN wallet_entry_timestamps b
                      ON a.contract = b.contract AND a.chain = b.chain AND a.wallet != b.wallet
                    WHERE a.wallet = ? AND a.chain = ?
                      AND (julianday(a.entry_ts) - julianday(b.entry_ts)) * 86400 BETWEEN ? AND ?
                    """,
                    (wallet_l, chain_l, _COPY_WINDOW_MIN_SECONDS, _COPY_WINDOW_MAX_SECONDS),
                )
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return CopyTradingFacts(available=False, error=f"correlation unavailable ({exc})")

    distinct_tokens = {r["contract"] for r in rows}
    followed = sorted({r["followed_wallet"] for r in rows})
    return CopyTradingFacts(
        distinct_tokens_followed=len(distinct_tokens), followed_wallets=followed, available=True,
    )
