"""Shadow correlation logging: was a smart-money wallet (leaderboard or
wallet_copy_shadow tracked) already holding a token BEFORE ARIA's own
technical-signal pockets (scalping_v8/v9) entered it? (16/08, operator
request -- v8/v9 show 0% winrate on 50 real closed paper trades, every
single exit is defensive (stop/timeout/invalidation), none ever reaches a
take-profit: the technical entry signal alone never predicts a real move.
Start accumulating this correlation data now, before a future
wallet-intelligence pocket (v11, #146) is built, so it has real shadow
history to calibrate against instead of starting from zero.

Pure observation, never a trigger: this module only RECORDS whether a
tracked/leaderboard wallet was already a holder at entry time. It never
gates, blocks, sizes, or influences any pocket's actual buy decision --
same doctrine as every other ``*_shadow.py`` module in this codebase
(``dip_recovery_shadow.py``, ``early_legitimacy_shadow.py``,
``candle_staleness_shadow.py``). Best-effort throughout: a failure here
must never affect the real position that just opened.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Scoped to the 2 pockets the 16/08 diagnostic actually covered (0% winrate,
# technical-signal-only entries) -- swing/vc weren't part of that finding,
# no reason to spend the extra Blockscout call on them yet. Extend here if a
# future session wants the same shadow data for another pocket.
_POCKETS_TRACKED = ("scalping_v8", "scalping_v9")


def gate_enabled() -> bool:
    return os.environ.get("ARIA_POCKET_SMART_MONEY_CORRELATION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pocket_smart_money_correlation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                pocket TEXT NOT NULL,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                smart_wallets_present TEXT NOT NULL DEFAULT '[]',
                smart_wallets_checked INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def _known_smart_wallets() -> set[str]:
    """Union of leaderboard (measured trading performance) + wallet_copy_shadow
    tracked (hand-picked + dynamic leaderboard-sourced) addresses, lowercased.
    Never raises -- either source failing degrades to a partial/empty set,
    this is a shadow observation, not a hard dependency on either."""
    wallets: set[str] = set()
    try:
        from aria_core.services import smart_money_leaderboard

        rows = await smart_money_leaderboard.get_leaderboard()
        wallets.update((row.get("wallet") or "").lower() for row in rows if row.get("wallet"))
    except Exception as exc:  # noqa: BLE001 -- shadow seam, never blocking
        logger.info("pocket_smart_money_correlation: leaderboard read failed (%s)", exc)
    try:
        from aria_core import wallet_copy_shadow

        wallets.update(w.lower() for w in wallet_copy_shadow.TRACKED_WALLETS)
        wallets.update(w.lower() for w in await wallet_copy_shadow._dynamic_tracked_wallets())
    except Exception as exc:  # noqa: BLE001
        logger.info("pocket_smart_money_correlation: tracked-wallet read failed (%s)", exc)
    wallets.discard("")
    return wallets


async def record_entry_correlation(position_id: int, pocket: str, contract: str, chain: str) -> None:
    """Best-effort, fire-and-forget -- call right after a v8/v9 position
    opens. Never raises, never blocks or slows down the caller beyond its
    own await (no retry, no long backoff: a missed observation this cycle
    is not worth risking the real trade path over)."""
    if not gate_enabled() or pocket not in _POCKETS_TRACKED:
        return
    try:
        known = await _known_smart_wallets()
        present: list[str] = []
        if known:
            from aria_core.services.blockscout import blockscout_client

            result = await blockscout_client.get_token_holders(contract)
            if result.available:
                holder_addresses = {h.address.lower() for h in result.holders if h.address}
                present = sorted(known & holder_addresses)

        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO pocket_smart_money_correlation_log "
                "(position_id, pocket, contract, chain, smart_wallets_present, "
                "smart_wallets_checked, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    position_id, pocket, contract.lower(), chain,
                    json.dumps(present), len(known),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never affect a real position
        logger.warning(
            "pocket_smart_money_correlation: record failed for position %s (%s)", position_id, exc,
        )


async def summary() -> dict:
    """Correlation readout: among closed v8/v9 positions, does having a
    known smart-money wallet present at entry predict a better outcome?
    Joins against ``paper_position``/``paper_position_archive`` (the real
    pnl_pct) -- returns ``{}`` fields as ``None`` until there's at least one
    closed, logged position on each side to compare (never a fabricated
    number on a near-empty sample, same doctrine as the rest of this
    codebase)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT position_id, pocket, smart_wallets_present FROM pocket_smart_money_correlation_log"
        )
        logged = await cursor.fetchall()
        if not logged:
            return {"logged_entries": 0, "with_smart_money": None, "without_smart_money": None}

        with_sm_ids = [pid for pid, _pocket, present in logged if json.loads(present)]
        without_sm_ids = [pid for pid, _pocket, present in logged if not json.loads(present)]

        async def _avg_pnl(ids: list[int]) -> dict | None:
            if not ids:
                return None
            placeholders = ",".join("?" * len(ids))
            pnls: list[float] = []
            for table in ("paper_position", "paper_position_archive"):
                cur = await db.execute(
                    f"SELECT pnl_pct FROM {table} WHERE id IN ({placeholders}) "
                    "AND status = 'closed' AND pnl_pct IS NOT NULL",
                    ids,
                )
                pnls.extend(row[0] for row in await cur.fetchall())
            if not pnls:
                return None
            wins = sum(1 for p in pnls if p > 0)
            return {
                "n_closed": len(pnls),
                "winrate_pct": round(100.0 * wins / len(pnls), 1),
                "avg_pnl_pct": round(sum(pnls) / len(pnls), 2),
            }

        return {
            "logged_entries": len(logged),
            "with_smart_money": await _avg_pnl(with_sm_ids),
            "without_smart_money": await _avg_pnl(without_sm_ids),
        }
