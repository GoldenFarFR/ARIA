"""Temporal stability confirmation on liquidity — VC screen.

Stress-test weak point (Codex Part 11, priority item #3): a `safety_screen`
scan reads liquidity/volume INSTANTANEOUSLY. A temporary manipulation
synchronized to the scan window (liquidity inflated just before
the scan, withdrawn right after) would pass the screen without anything
detecting it — a single reading can, by construction, never prove stability
over time.

Design chosen, honest about its limits: unlike momentum wash-trading
(in-process memory state, confirmed via repeated scans in a continuous loop),
the VC screen isn't a continuous cycle — a contract may only ever be scanned
once. Confirmation can therefore ONLY apply if this same contract has
already been seen by a previous scan, within a recent window (persisted in the DB,
survives restarts, unlike a simple process dict). On a
FIRST scan (no prior history), the result is `None` (indeterminate) — never
a rejection due to missing data, same fail-open doctrine as the rest of the
project when information is missing to judge.
"""
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Liquidity drop between two scans beyond which a
# withdrawal is suspected (not just the normal volatility of a thin market) -- soft-fail,
# never a confirmed mechanism in the contract (market behavior).
DEFAULT_MAX_DROP_PCT = 40.0
# Window within which a previous scan is considered relevant for
# comparison -- beyond it, a real, legitimate market move (not a
# manipulation synchronized to ONE scan) becomes more likely.
DEFAULT_WINDOW_MINUTES = 60


@dataclass(frozen=True)
class LiquidityStabilityResult:
    confirmed: bool | None  # True=stable, False=suspicious drop, None=no prior history
    previous_liquidity_usd: float | None = None
    previous_recorded_at: str | None = None


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vc_liquidity_snapshots (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                liquidity_usd REAL NOT NULL,
                volume_24h_usd REAL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


async def record_and_check_liquidity_stability(
    contract: str,
    chain: str,
    liquidity_usd: float,
    volume_24h_usd: float | None = None,
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    max_drop_pct: float = DEFAULT_MAX_DROP_PCT,
) -> LiquidityStabilityResult:
    """Compares current liquidity to the last known snapshot (if it exists and
    stays within the window), THEN records the current snapshot (upsert -- only
    one snapshot kept per contract, the most recent, not a full history).

    Always records the new snapshot, even if no comparison was
    possible -- without this, a contract never seen again would never benefit from
    the protection on the next scan."""
    contract_l = (contract or "").strip().lower()
    chain_l = (chain or "").strip().lower()
    if not contract_l or not chain_l or liquidity_usd is None or liquidity_usd < 0:
        return LiquidityStabilityResult(confirmed=None)

    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT liquidity_usd, recorded_at FROM vc_liquidity_snapshots "
                "WHERE contract = ? AND chain = ? "
                "AND recorded_at >= datetime('now', ?)",
                (contract_l, chain_l, f"-{window_minutes} minutes"),
            )
        ).fetchone()

        await db.execute(
            "INSERT INTO vc_liquidity_snapshots (contract, chain, liquidity_usd, volume_24h_usd, recorded_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(contract, chain) DO UPDATE SET "
            "liquidity_usd = excluded.liquidity_usd, volume_24h_usd = excluded.volume_24h_usd, "
            "recorded_at = excluded.recorded_at",
            (contract_l, chain_l, liquidity_usd, volume_24h_usd),
        )
        await db.commit()

    if row is None:
        return LiquidityStabilityResult(confirmed=None)

    previous = float(row["liquidity_usd"])
    if previous <= 0:
        return LiquidityStabilityResult(confirmed=None, previous_liquidity_usd=previous, previous_recorded_at=row["recorded_at"])

    drop_pct = 100.0 * (previous - liquidity_usd) / previous
    confirmed = drop_pct < max_drop_pct
    return LiquidityStabilityResult(
        confirmed=confirmed, previous_liquidity_usd=previous, previous_recorded_at=row["recorded_at"],
    )
