"""Pool of "screened" tokens — the reservoir the loop draws its 20 from.

A token that **passes the filter** (`skills/safety_screen.py`) enters here.
Every Monday, the training loop draws **20 random candidates** from the
active pool (lottery) → an **unbiased** sample (no cherry-picking) AND
**screened** (not a technical scam). A token can be re-checked and
**removed** (`dropped`) if it degrades (leaking liquidity, unlocked LP) — a
clean contract today may not stay clean tomorrow.

Local SQLite storage in `aria.db`, table `screened_token` (key = contract).
No financial action: this is a directory of candidates.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "contract",
    "symbol",
    "liquidity_usd",
    "security_score",
    "top_holder_pct",
    "verdict",
    "pool_address",
    "network",
    "status",
    "first_screened_at",
    "last_checked_at",
    "screen_reason",
    "retry_count",
    "source",
]

# Columns added after the fact: (name, SQL definition) for the ALTER migration
# (same pattern as `vc_predictions.py`/`exam.py` — SQLite doesn't create them on
# a pre-existing table, only `CREATE TABLE IF NOT EXISTS`).
_ADDED_COLUMNS = [
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    # Origin discovery pipeline ('top_pools' / 'radar_x' / ...): empty string on
    # historical rows (never NULL, never an opaque rejection). Follow-up to the
    # #77 diversification audit (12/07): without this, there's no objective way
    # to measure which pipeline contributes noise (hard failures) vs signal.
    ("source", "TEXT NOT NULL DEFAULT ''"),
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS screened_token (
                contract TEXT PRIMARY KEY,
                symbol TEXT,
                liquidity_usd REAL,
                security_score INTEGER,
                top_holder_pct REAL,
                verdict TEXT,
                pool_address TEXT,
                network TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                first_screened_at TEXT NOT NULL,
                last_checked_at TEXT NOT NULL,
                screen_reason TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # Hot migration: adds missing columns to existing DBs
        # (SQLite doesn't create them if the table pre-exists). Idempotent, non-destructive.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(screened_token)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE screened_token ADD COLUMN {name} {ddl}")
        await db.commit()


async def upsert_screened(
    *,
    contract: str,
    symbol: str = "",
    liquidity_usd: float = 0.0,
    security_score: int = 0,
    top_holder_pct: float | None = None,
    verdict: str = "",
    pool_address: str = "",
    network: str = "base",
    screen_reason: str = "",
    source: str = "",
) -> None:
    """Adds/refreshes a screened token (status ``active``).

    Upsert: ``first_screened_at`` is preserved on re-registration (keeps the
    first-entry date), ``last_checked_at`` is always updated. Re-activating
    (`active`) a token that passes the filter again is intentional.
    ``retry_count`` is reset to zero: once active, the "pending" retry counter
    no longer means anything — if it degrades again later, it starts a fresh
    retry budget. ``source`` (optional, e.g. ``'top_pools'``/``'radar_x'``):
    origin discovery pipeline, preserved on re-registration like
    ``first_screened_at`` (doesn't overwrite an already-known source if the
    caller doesn't specify one).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason, retry_count, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, 0, ?)
            ON CONFLICT(contract) DO UPDATE SET
              symbol=excluded.symbol,
              liquidity_usd=excluded.liquidity_usd,
              security_score=excluded.security_score,
              top_holder_pct=excluded.top_holder_pct,
              verdict=excluded.verdict,
              pool_address=excluded.pool_address,
              network=excluded.network,
              status='active',
              last_checked_at=excluded.last_checked_at,
              screen_reason=excluded.screen_reason,
              retry_count=0,
              source=CASE WHEN excluded.source != '' THEN excluded.source ELSE screened_token.source END
            """,
            (
                contract, symbol, liquidity_usd, security_score, top_holder_pct,
                verdict, pool_address, network, now, now, screen_reason, source,
            ),
        )
        await db.commit()


async def record_rejected(
    *, contract: str, reason: str = "", symbol: str = "", network: str = "base",
    source: str = "", liquidity_usd: float = 0.0, security_score: int = 0,
    verdict: str = "", top_holder_pct: float | None = None,
) -> None:
    """Marks a contract as rejected ("thrown away for good"), with its reason.

    Kept IN THE DATABASE (status ``rejected``) rather than ignored: this avoids
    re-scanning it endlessly (intransigence = efficient), and allows a
    targeted **resurrection** if noise reappears (see ``reconsider``). Upsert:
    ``first_screened_at`` preserved. ``source``: same logic as
    ``upsert_screened`` (preserved if not specified on re-registration).

    ``liquidity_usd``/``security_score``/``verdict``/``top_holder_pct``
    (optional, 15/07, same fix as ``record_pending``): pass the real scan
    values when the caller already has them (rejection AFTER a complete scan),
    rather than leaving them at 0/''/NULL — otherwise a hard rejection
    (honeypot, catastrophic score) is indistinguishable from a rejection whose
    score was never known. Defaults preserved for a caller with no scan.
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason, source)
            VALUES (?, ?, ?, ?, ?, ?, '', ?, 'rejected', ?, ?, ?, ?)
            ON CONFLICT(contract) DO UPDATE SET
              status='rejected', last_checked_at=excluded.last_checked_at,
              screen_reason=excluded.screen_reason,
              liquidity_usd=excluded.liquidity_usd,
              security_score=excluded.security_score,
              verdict=excluded.verdict,
              top_holder_pct=excluded.top_holder_pct,
              source=CASE WHEN excluded.source != '' THEN excluded.source ELSE screened_token.source END
            """,
            (
                contract, symbol, liquidity_usd, security_score, top_holder_pct,
                verdict, network, now, now, reason, source,
            ),
        )
        await db.commit()


async def record_pending(
    *, contract: str, reason: str = "", symbol: str = "", network: str = "base",
    source: str = "", liquidity_usd: float = 0.0, security_score: int = 0,
    verdict: str = "", top_holder_pct: float | None = None,
) -> None:
    """Marks a contract as "to revisit" (soft failure, unavailable data), with
    its reason — never a definitive rejection.

    Unlike ``record_rejected``, ``status='pending'`` does NOT short-circuit
    re-scanning (``get_status`` only blocks on 'rejected'/'active'): the
    contract will be retried on the next cycle. Goal: the reason for a soft
    failure (holders not returned, unverified contract, etc.) leaves a
    queryable trace rather than vanishing with no data at all, anywhere
    (see audit #77).

    ``liquidity_usd``/``security_score``/``verdict`` (optional, 15/07): when
    the caller already has a complete scan in hand (soft failure AFTER the
    scan, e.g. ``token_absorber.absorb`` on unknown holders), pass the real
    computed values rather than leaving them at 0 — before this fix, a
    promising pending candidate (correct score/liquidity, just one missing
    side-datum) was indistinguishable from a pending candidate with no signal
    at all, preventing any ranking by proximity to the threshold (see
    ``list_closest_to_passing``). Default 0/'' preserved for a caller that
    does NOT yet have a scan (e.g. Volet C pre-filter) — never a made-up value.

    ``retry_count`` increments on every call (1 on the first soft failure, +1
    on every re-pass — whether it's a chance rediscovery or a deliberate
    retry, same function for both, see ``token_absorber.absorb``): this is
    the counter that ``abandon_stale_pending`` reads to stop insisting on a
    signal that never matures (see #77/#105 audit follow-up). ``source``:
    same logic as ``upsert_screened`` (preserved if not specified on
    re-registration).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason, retry_count, source)
            VALUES (?, ?, ?, ?, ?, ?, '', ?, 'pending', ?, ?, ?, 1, ?)
            ON CONFLICT(contract) DO UPDATE SET
              status='pending', last_checked_at=excluded.last_checked_at,
              screen_reason=excluded.screen_reason,
              liquidity_usd=excluded.liquidity_usd,
              security_score=excluded.security_score,
              verdict=excluded.verdict,
              top_holder_pct=excluded.top_holder_pct,
              retry_count=screened_token.retry_count + 1,
              source=CASE WHEN excluded.source != '' THEN excluded.source ELSE screened_token.source END
            """,
            (
                contract, symbol, liquidity_usd, security_score, top_holder_pct,
                verdict, network, now, now, reason, source,
            ),
        )
        await db.commit()


async def get_status(contract: str) -> str | None:
    """Known status of a contract (active / rejected / dropped), or None if never seen."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute("SELECT status FROM screened_token WHERE contract=?", (contract,))
        ).fetchone()
    return row[0] if row else None


async def reconsider(contract: str) -> bool:
    """Noise has reappeared: reopens a rejected token for re-evaluation. True if applicable.

    Only LIFTS the "thrown away for good" status (-> pending); the real
    decision goes back to the on-chain re-scan (noise filters/wakes it up, it
    doesn't decide). Returns False if the contract is unknown or already
    active. ``retry_count`` restarts at zero: an external signal that
    justifies the resurrection deserves a fresh retry budget, not the
    continuation of a counter from a previous life (including for a contract
    already abandoned by ``abandon_stale_pending``).
    """
    status = await get_status(contract)
    if status not in ("rejected", "dropped"):
        return False
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE screened_token SET status='pending', last_checked_at=?, "
            "retry_count=0 WHERE contract=?",
            (now, contract),
        )
        await db.commit()
    return True


async def drop_token(contract: str, *, reason: str = "") -> None:
    """Removes a token from the active pool (degraded). Stays in the database (status ``dropped``)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE screened_token SET status='dropped', last_checked_at=? WHERE contract=?",
            (now, contract),
        )
        await db.commit()


async def list_stale_pending(
    *, older_than_hours: int = 24, limit: int = 20, network: str = "base"
) -> list[dict]:
    """``pending`` candidates whose last check is at least ``older_than_hours`` old.

    'pending' == soft failure (data not yet mature: contract not yet verified,
    holders not yet readable, liquidity not yet up...) — never a definitive
    rejection (see ``record_pending``), but nothing PROACTIVELY retries it
    today: only a chance rediscovery (the same contract reappearing in
    ``discover_top_pools``/``discover_direct_candidates``) triggers a
    re-scan. This list serves as a queue for a deliberate retry (see
    ``base_crawler.retry_stale_pending``), not a new filtering mechanism —
    ``token_absorber.absorb`` (already called without a short-circuit on
    'pending') does all the re-evaluation work.
    """
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM screened_token WHERE status='pending' AND network=? "
                "AND last_checked_at <= ? ORDER BY last_checked_at ASC LIMIT ?",
                (network, cutoff, limit),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def abandon_stale_pending(
    contract: str, *, max_retries: int = 5, max_age_days: int = 7
) -> bool:
    """Moves a never-ending ``pending`` to a terminal state (``rejected``).

    A candidate stuck in a soft failure indefinitely (never active, never a
    real confirmed malicious ``hard_fail``) would otherwise stay ``pending``
    forever: retried on every ``retry_stale_pending`` cycle (audit #77), an
    API scan every 24h with no end for a signal that never matures. **This is
    NOT a new security criterion** — no duplicated filter, ``safety_screen``/
    ``token_absorber`` unchanged, the `passed` threshold stays the same —
    only a limit on the NUMBER OF PASSES: beyond ``max_retries`` attempts OR
    ``max_age_days`` days since ``first_screened_at``, we stop insisting and
    classify it definitively, keeping the last known soft reason as a trace
    (never an empty field, same doctrine as ``record_pending``/``record_rejected``).

    Returns False (no-op) if the contract is unknown, is no longer
    ``pending``, or hasn't yet exceeded the thresholds — called by
    ``base_crawler.retry_stale_pending`` only after a new confirmed soft
    failure (``token_absorber.absorb`` has already decided: still SOFT,
    neither matured to ``active`` nor a real malicious rejection).
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT status, first_screened_at, retry_count, screen_reason "
                "FROM screened_token WHERE contract=?",
                (contract,),
            )
        ).fetchone()
        if row is None or row[0] != "pending":
            return False
        _status, first_screened_at, retry_count, last_reason = row
        age_days = (
            datetime.now(timezone.utc) - datetime.fromisoformat(first_screened_at)
        ).total_seconds() / 86_400
        if retry_count < max_retries and age_days < max_age_days:
            return False
        now = datetime.now(timezone.utc).isoformat()
        reason = (
            f"abandonné après {retry_count} tentatives ({age_days:.1f}j) — signal "
            f"faible persistant : {last_reason or 'raison indisponible'}"
        )
        await db.execute(
            "UPDATE screened_token SET status='rejected', last_checked_at=?, "
            "screen_reason=? WHERE contract=?",
            (now, reason, contract),
        )
        await db.commit()
    return True


async def list_pool(status: str = "active", limit: int = 1000, *, network: str = "base") -> list[dict]:
    """``network="base"`` by default preserves EXACTLY the historical behavior
    (the 85% VC pool has never written anything other than
    ``network="base"``). The bonding pool (15% niche, see
    ``bonding_absorber.py``) lives under ``network="base-bonding"`` — never
    mixed in without an explicit call, so as not to contaminate the weekly
    draw (``weekly_training.draw_lottery`` stays 100% VC pool, unchanged)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM screened_token WHERE status=? AND network=? "
                "ORDER BY last_checked_at DESC LIMIT ?",
                (status, network, limit),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def count_pool(status: str = "active", *, network: str = "base") -> int:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM screened_token WHERE status=? AND network=?",
                (status, network),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def draw_lottery(n: int = 20, *, status: str = "active", network: str = "base") -> list[dict]:
    """Draws ``n`` tokens AT RANDOM from the active pool (unbiased sample).

    If the pool contains fewer than ``n`` tokens, returns the whole pool
    (shuffled). The random draw is what prevents cherry-picking: ARIA
    doesn't pick "the ones that suit it", chance decides within an
    already-screened reservoir.
    """
    pool = await list_pool(status=status, limit=100_000, network=network)
    if n <= 0 or not pool:
        return []
    if len(pool) <= n:
        random.shuffle(pool)
        return pool
    return random.sample(pool, n)


_LIQUIDITY_TARGET_USD = 30_000.0


async def list_closest_to_passing(*, network: str = "base", limit: int = 3) -> list[dict]:
    """Ranks ``pending`` candidates by proximity to the security threshold — real
    entry points to watch rather than a simple active/not-active binary count
    (operator request 14/07, see CLAUDE.md). An informational heuristic, not
    an official score: highest security score first (closest to clearing the
    ``safety_screen`` threshold from below), then liquidity closest to
    30,000$ (the usual floor) as a tiebreaker. A missing value (``None``) is
    relegated to the end of the ranking rather than skewing the sort.
    """
    pool = await list_pool(status="pending", limit=100_000, network=network)

    def _rank(entry: dict) -> tuple[float, float]:
        score = entry.get("security_score")
        score_component = -float(score) if isinstance(score, (int, float)) else 0.0
        liquidity = entry.get("liquidity_usd")
        liquidity_gap = (
            abs(_LIQUIDITY_TARGET_USD - float(liquidity))
            if isinstance(liquidity, (int, float))
            else float("inf")
        )
        return (score_component, liquidity_gap)

    return sorted(pool, key=_rank)[:limit]
