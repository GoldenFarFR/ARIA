"""Per-creator reputation for Solana fresh launches (20/08).

**The signal, measured on 1500 real closures before any of this was built.**
Grouping every closed shadow position by its RugCheck `creator` wallet splits
the dome cleanly in two:

    creator with 1-3 tokens   n=985   winrate 15.3-15.5%   PnL -2857 pts
    creator with 4+ tokens    n=515   winrate  4.7%        PnL -3241 pts

The "token factory" side carries 34% of the volume but MORE THAN HALF of all
losses, while holding only 5 of the 36 positions that ever returned over x2.
Some wallets in the data launched 60, 34 and 33 separate tokens. A factory
wallet is not a builder, and its next launch behaves like its last ones.

**Why a persistent blacklist rather than a live lookup.** RugCheck only
reveals `creator` on its async post-entry backfill, so the creator is unknown
at the moment the entry decision is made -- a live lookup would reintroduce
exactly the multi-minute wait the pockets' fire-and-forget design exists to
avoid. Instead every backfill FEEDS this table, and the entry path does a
local indexed read: zero latency, zero API call, and the knowledge compounds
with every token observed.

**Why this filter is allowed to touch entry at all.** The same audit
established that 1.8% of trades carry 100% of the gain, so an entry filter is
normally the WRONG lever -- it risks cutting the rare winners. This one is
the exception, and only because the numbers say so explicitly: the segment it
cuts holds 14% of the big winners for 34% of the volume and over half the
losses. Any future entry filter must clear that same bar (measured winner
share vs volume share) before being added.

Honest limits, stated rather than discovered later:
  - The threshold is a COUNT OF TOKENS OBSERVED BY ARIA, not a token count on
    chain. A prolific creator ARIA has only seen twice reads as "new" here.
    It can only ever under-flag, never over-flag, which is the safe direction.
  - This is in-sample. `MIN_TOKENS_FOR_FACTORY` was not fitted -- 4 is simply
    where the measured winrate cliff sits (15.5% -> 4.7%), not a value swept
    for the best PnL.
"""
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from .paths import shadow_db_path

TABLE = "solana_creator_reputation"

# Where the measured winrate cliff sits (15.5% at 2-3 tokens, 4.7% at 4+).
# Deliberately NOT swept for the best backtest PnL -- that would be fitting
# the threshold to this exact sample.
MIN_TOKENS_FOR_FACTORY = 4

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return str(shadow_db_path())


@dataclass
class CreatorStats:
    creator: str
    tokens_seen: int
    is_factory: bool


async def _ensure_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                creator TEXT PRIMARY KEY,
                tokens_seen INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT,
                last_seen_at TEXT
            )
            """
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def record_creator(creator: str | None, *, seen_at: str, db_path: str | None = None) -> None:
    """Called from the RugCheck backfill. Best-effort: never raises into the
    enrichment task."""
    if not creator:
        return
    try:
        path = db_path or _db_path()
        await _ensure_table(path)
        async with aiosqlite.connect(path) as db:
            await db.execute(
                f"""
                INSERT INTO {TABLE} (creator, tokens_seen, first_seen_at, last_seen_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(creator) DO UPDATE SET
                    tokens_seen = tokens_seen + 1, last_seen_at = excluded.last_seen_at
                """,
                (creator, seen_at, seen_at),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 -- reputation bookkeeping never breaks enrichment
        return


async def get_stats(creator: str | None, *, db_path: str | None = None) -> CreatorStats | None:
    """``None`` when the creator is unknown -- explicitly NOT a factory verdict.
    An unseen creator must read as "no information", never as "clean" and never
    as "suspect": the count only ever under-flags (see module docstring)."""
    if not creator:
        return None
    try:
        path = db_path or _db_path()
        await _ensure_table(path)
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(f"SELECT * FROM {TABLE} WHERE creator = ?", (creator,))
            row = await cur.fetchone()
        if row is None:
            return None
        seen = row["tokens_seen"]
        return CreatorStats(creator=creator, tokens_seen=seen, is_factory=seen >= MIN_TOKENS_FOR_FACTORY)
    except Exception:  # noqa: BLE001
        return None


async def is_factory(creator: str | None, *, db_path: str | None = None) -> bool:
    """Fail-OPEN on purpose, unlike the holder gate. A missing reputation row
    means "never observed", which is the NORMAL state for a genuinely new
    builder -- rejecting on it would block every real fresh launch, the exact
    opposite of the intent. The holder gate stays fail-closed because there an
    absent answer means an outage, not a legitimate absence."""
    stats = await get_stats(creator, db_path=db_path)
    return bool(stats and stats.is_factory)


async def backfill_from_closed_positions(*, db_path: str | None = None) -> int:
    """Seeds the table from the creators already recorded on past shadow rows,
    so the filter starts with the 1500 closures' worth of knowledge instead of
    relearning it from zero. Idempotent: rebuilds the counts from scratch."""
    path = db_path or _db_path()
    await _ensure_table(path)
    total = 0
    async with aiosqlite.connect(path) as db:
        await db.execute(f"DELETE FROM {TABLE}")
        for table in (
            "solana_fresh_launch_fast_discovery_shadow_log",
            "solana_fresh_launch_ws_exit_shadow_log",
        ):
            try:
                await db.execute(
                    f"""
                    INSERT INTO {TABLE} (creator, tokens_seen, first_seen_at, last_seen_at)
                    SELECT rugcheck_creator, COUNT(*), MIN(detected_at), MAX(detected_at)
                    FROM {table} WHERE rugcheck_creator IS NOT NULL AND rugcheck_creator <> ''
                    GROUP BY rugcheck_creator
                    ON CONFLICT(creator) DO UPDATE SET
                        tokens_seen = tokens_seen + excluded.tokens_seen,
                        last_seen_at = MAX(last_seen_at, excluded.last_seen_at)
                    """
                )
            except aiosqlite.OperationalError:
                continue  # a pocket whose table does not exist yet
        await db.commit()
        cur = await db.execute(f"SELECT COUNT(*) FROM {TABLE}")
        total = (await cur.fetchone())[0]
    return total
