"""Wallet-copy shadow -- forward-tests copying 8 verified/labeled Base wallets.

08/08, operator direction after the fomo-leaderboard investigation: the
displayed leaderboard PnL is largely fictional (top-ranked "change" shows
+$2.58M on the leaderboard vs +$14.8K real realized PnL once independently
verified via fomoscan.sh; several other "top" traders were net negative once
verified the same way). Never copy a wallet blindly (CLAUDE.md smart-money
doctrine: confirmation, never a trigger) -- this shadow answers the prior
question empirically: does copying each of these wallets, ONE AT A TIME,
independently, actually produce alpha over time? Same never-route-a-real-trade
doctrine as narrative_signal_shadow.py / v8_limit_shadow.py.

8 tracked wallets, two tiers of evidence (kept honest, never blended):
- 3 with a long, fully-verified all-time track record (fomoscan.sh): songz
  (+$177,213 realized, 52.1% win rate), thokani (+$119,241, 45.2%), wrld_sol
  (+$74,277, 27.7%).
- 4 labeled "Smart Money" by GMGN on Base with a positive 30d realized PnL --
  weaker evidence, a short window, not independently re-verified beyond
  GMGN's own label.
- 1 confirmed serial front-runner (Lookonchain: "Base is for everyone",
  $231.8k profit buying minutes before Base's own announcement, 17/04/2025)
  -- independently found here to have also bought AEON 2 days before the
  04/08/2026 Programmable partnership announcement. Included NOT as a "good
  trader" but to test whether following a known front-runner is itself
  profitable, separately from the other 7.

Operator's explicit design: "chaque transaction sera adossé a sont wallet
copié" -- each tracked wallet gets its OWN isolated paper ledger, never
merged. A fixed ``POSITION_SIZE_USD`` fictional stake per detected buy,
mirrored close when the source wallet sells the same token. Polled via
Blockscout (``get_token_transfers``), a persisted per-wallet cursor (last
tx_hash seen) keeps each scan incremental -- never reprocesses the same
transfer twice.

Deliberately simple for a first pass: one open position per (wallet,
contract) at a time (a second buy of an already-open position is ignored,
not averaged in); the mark-to-market price on open positions is refreshed
opportunistically during each scan pass, not on a dedicated schedule.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Fictional stake per detected buy -- never real capital, purely a common
# yardstick so every copied wallet is compared on equal footing regardless
# of the source wallet's own real position sizing.
POSITION_SIZE_USD = 1_000.0

# 08/08 -- operator concern, verified real: some tracked wallets rotate
# (the leaderboard's "all-time" history can outlive the specific on-chain
# address currently associated with a handle -- confirmed live for songz,
# see TRACKED_WALLETS below). No reliable automatic way to discover a
# trader's NEW address exists here (would require re-scraping fomoscan/GMGN
# on a schedule, too fragile for a heartbeat cycle) -- what IS reliably
# buildable: flag a tracked wallet as dormant once it goes quiet, so a
# session can notice and manually look up whether the trader moved.
INACTIVITY_THRESHOLD_DAYS = 14

# Transfers of these tokens are the PAYMENT leg of a swap (WETH/USDC/etc in,
# token-of-interest out, or vice versa) -- never themselves the "buy"/"sell"
# to copy. Base addresses, lowercase.
_QUOTE_TOKENS = {
    "0x4200000000000000000000000000000000000006",  # WETH
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC (bridged)
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI (Base)
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",  # cbBTC
}

# Two tiers of evidence, kept explicit in the label -- never blended in
# reporting. Source: this session's research (fomoscan.sh + GMGN Base Smart
# Money rank + the front-runner cross-check against Lookonchain's "Base is
# for everyone" case).
TRACKED_WALLETS: dict[str, dict] = {
    "0xd3a61ba3bd055f6aa962cc7554e117b4baf8d0a5": {
        "label": "songz", "tier": "verified_all_time",
        "evidence": (
            "fomoscan: +$177,213 realized all-time, 52.1% win rate -- CAVEAT "
            "(verified 08/08, wallet_age_check): this Base address's first "
            "on-chain tx is 2026-07-12, ~1 month old, ~150 ERC-20 transfers "
            "total -- cannot possibly account for the 2,432-trade all-time "
            "history fomoscan attributes to the 'songz' handle. Likely wallet "
            "rotation on the app's side (a new address behind the same handle) "
            "or cross-chain activity (fomo is multi-chain) not visible here. "
            "Treat the 'all-time' evidence as attached to the HANDLE, not "
            "necessarily to this specific address's own history."
        ),
        "wallet_first_tx_at": "2026-07-12",
    },
    "0xe6a5f7f6de90d5693fac766fca4d0d3214f95083": {
        "label": "thokani", "tier": "verified_all_time",
        "evidence": "fomoscan: +$119,241 realized all-time, 45.2% win rate",
        "wallet_first_tx_at": "2025-11-07",
    },
    "0xeea1a89465e31fbd95ab99b2f81ab3b974cb674e": {
        "label": "wrld_sol", "tier": "verified_all_time",
        "evidence": "fomoscan: +$74,277 realized all-time, 27.7% win rate",
        "wallet_first_tx_at": "2026-02-14",
    },
    "0x8d73a36d78e2ae4a437053c9ce3be70d483ab74d": {
        "label": "gmgn_0x8d_b74d", "tier": "gmgn_smart_money_30d",
        "evidence": "GMGN Base Smart Money: +$4,210/30d (1.86x)",
        "wallet_first_tx_at": "2024-01-01",
    },
    "0xfd7e55a555555c2f25053a38ec744de1afea4fa4": {
        "label": "gmgn_0xfd_4fa4", "tier": "gmgn_smart_money_30d",
        "evidence": "GMGN Base Smart Money: +$4,150/30d (2.49x)",
        "wallet_first_tx_at": "2024-07-16",
    },
    "0xa83b73f5644cde337b61da79589f10ea15548811": {
        "label": "gmgn_antpositions", "tier": "gmgn_smart_money_30d",
        "evidence": "GMGN Base Smart Money: +$2,640/30d",
        "wallet_first_tx_at": "2025-10-21",
    },
    "0xb226f97bc5b01978848dc440b40c70faea7c006e": {
        "label": "gmgn_alexwong", "tier": "gmgn_smart_money_30d",
        "evidence": "GMGN Base Smart Money: +$139/30d (low volume)",
        "wallet_first_tx_at": "2025-10-19",
    },
    "0xbd3183a293b3bea81479be052491207f1a328adf": {
        "label": "serial_frontrunner", "tier": "frontrunner",
        "evidence": (
            "Lookonchain: $231.8k profit front-running Base's own "
            "'Base is for everyone' announcement (17/04/2025); independently "
            "found buying AEON 2 days before the 04/08/2026 Programmable "
            "partnership announcement"
        ),
        "wallet_first_tx_at": "2024-06-01",
    },
}

_POSITION_DDL = """
CREATE TABLE IF NOT EXISTS wallet_copy_shadow_position (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    wallet_label TEXT NOT NULL,
    contract TEXT NOT NULL,
    chain TEXT NOT NULL DEFAULT 'base',
    symbol TEXT,
    entry_tx_hash TEXT NOT NULL,
    entry_price_usd REAL,
    entry_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    exit_tx_hash TEXT,
    exit_price_usd REAL,
    exit_at TEXT,
    last_mark_price_usd REAL,
    last_mark_at TEXT
)
"""
_CURSOR_DDL = """
CREATE TABLE IF NOT EXISTS wallet_copy_shadow_cursor (
    wallet_address TEXT PRIMARY KEY,
    last_tx_hash TEXT,
    last_scanned_at TEXT NOT NULL,
    last_transfer_at TEXT
)
"""

_table_ready = False


async def _ensure_tables() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_POSITION_DDL)
        await db.execute(_CURSOR_DDL)
        # Hot migration (08/08): last_transfer_at added after this table was
        # first deployed -- a prod DB that already ran a cycle needs it added
        # in place, same pattern as paper_trader.py's _ADDED_COLUMNS.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(wallet_copy_shadow_cursor)")).fetchall()
        }
        if "last_transfer_at" not in existing:
            await db.execute("ALTER TABLE wallet_copy_shadow_cursor ADD COLUMN last_transfer_at TEXT")
        await db.commit()
    _table_ready = True


@dataclass(frozen=True)
class ShadowScanResult:
    wallet_address: str
    opened: int
    closed: int
    error: str | None = None


async def _get_cursor(wallet: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT last_tx_hash FROM wallet_copy_shadow_cursor WHERE wallet_address = ?",
            (wallet,),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def _get_last_transfer_at(wallet: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT last_transfer_at FROM wallet_copy_shadow_cursor WHERE wallet_address = ?",
            (wallet,),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def _set_cursor(wallet: str, last_tx_hash: str | None, *, last_transfer_at: str | None = None) -> None:
    """``last_transfer_at`` (08/08): the ON-CHAIN timestamp of the newest
    transfer actually observed this scan (never the scan's own wall-clock
    time) -- only advanced when a newer transfer was seen, so a scan that
    finds nothing new never resets the activity clock."""
    async with aiosqlite.connect(DB_PATH) as db:
        if last_transfer_at is not None:
            await db.execute(
                "INSERT INTO wallet_copy_shadow_cursor "
                "(wallet_address, last_tx_hash, last_scanned_at, last_transfer_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(wallet_address) DO UPDATE SET last_tx_hash = excluded.last_tx_hash, "
                "last_scanned_at = excluded.last_scanned_at, last_transfer_at = excluded.last_transfer_at",
                (wallet, last_tx_hash, datetime.now(timezone.utc).isoformat(), last_transfer_at),
            )
        else:
            await db.execute(
                "INSERT INTO wallet_copy_shadow_cursor (wallet_address, last_tx_hash, last_scanned_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(wallet_address) DO UPDATE SET last_tx_hash = excluded.last_tx_hash, "
                "last_scanned_at = excluded.last_scanned_at",
                (wallet, last_tx_hash, datetime.now(timezone.utc).isoformat()),
            )
        await db.commit()


async def _open_position_exists(wallet: str, contract: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM wallet_copy_shadow_position "
            "WHERE wallet_address = ? AND contract = ? AND status = 'open' LIMIT 1",
            (wallet, contract),
        )
        return (await cursor.fetchone()) is not None


async def _open_position(
    wallet: str, label: str, contract: str, symbol: str | None,
    tx_hash: str, price_usd: float | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_copy_shadow_position "
            "(wallet_address, wallet_label, contract, symbol, entry_tx_hash, "
            " entry_price_usd, entry_at, last_mark_price_usd, last_mark_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                wallet, label, contract, symbol, tx_hash, price_usd,
                datetime.now(timezone.utc).isoformat(), price_usd,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    logger.info(
        "wallet_copy_shadow: %s opened %s (%s) at $%s -- mirrors %s",
        label, symbol or contract[:10], tx_hash[:10], price_usd, wallet[:10],
    )


async def _close_open_position(
    wallet: str, contract: str, tx_hash: str, price_usd: float | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE wallet_copy_shadow_position "
            "SET status = 'closed', exit_tx_hash = ?, exit_price_usd = ?, exit_at = ? "
            "WHERE wallet_address = ? AND contract = ? AND status = 'open'",
            (tx_hash, price_usd, datetime.now(timezone.utc).isoformat(), wallet, contract),
        )
        await db.commit()
    logger.info(
        "wallet_copy_shadow: %s closed position on %s at $%s (tx %s)",
        wallet[:10], contract[:10], price_usd, tx_hash[:10],
    )


async def _mark_open_position(wallet: str, contract: str, price_usd: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE wallet_copy_shadow_position "
            "SET last_mark_price_usd = ?, last_mark_at = ? "
            "WHERE wallet_address = ? AND contract = ? AND status = 'open'",
            (price_usd, datetime.now(timezone.utc).isoformat(), wallet, contract),
        )
        await db.commit()


async def _current_price_usd(contract: str) -> float | None:
    """Best-effort spot price -- never a guess, ``None`` if no liquid pair is
    found (same degradation as every other pair lookup in this codebase)."""
    try:
        from aria_core.services.dexscreener import fetch_token_pairs

        pairs = await fetch_token_pairs(contract, chain="base")
        if not pairs:
            return None
        best = max(pairs, key=lambda p: p.liquidity_usd or 0.0)
        return best.price_usd if best.price_usd and best.price_usd > 0 else None
    except Exception as exc:  # noqa: BLE001 -- shadow, never blocking
        logger.info("wallet_copy_shadow: price lookup failed for %s (%s)", contract, exc)
        return None


async def scan_wallet(wallet: str, meta: dict) -> ShadowScanResult:
    """One incremental pass over ``wallet``'s recent Base ERC-20 transfers --
    opens a fictional position on a detected buy (received a non-quote
    token), closes the matching open one on a detected sell (sent a
    non-quote token it currently holds a shadow position in). Best-effort:
    a scan failure never raises, never blocks the other 7 wallets."""
    label = meta["label"]
    try:
        await _ensure_tables()
        from aria_core.services.blockscout import blockscout_client

        result = await blockscout_client.get_token_transfers(
            wallet, limit=50, max_pages=2, token_type="ERC-20",
        )
        if not result.available or not result.transfers:
            return ShadowScanResult(wallet, 0, 0, error=result.error)

        last_seen = await _get_cursor(wallet)
        # Blockscout returns newest-first -- process oldest-to-newest so
        # opens/closes land in the order they actually happened on-chain.
        transfers = list(reversed(result.transfers))
        if last_seen:
            seen_hashes = {t.tx_hash for t in transfers}
            if last_seen in seen_hashes:
                idx = next(i for i, t in enumerate(transfers) if t.tx_hash == last_seen)
                transfers = transfers[idx + 1:]
            # else: cursor fell outside this window (wallet very active
            # since last scan) -- process the whole window, best-effort,
            # never blocks waiting for a gap-free history.

        opened = closed = 0
        newest_hash = last_seen
        newest_transfer_at = None
        for t in transfers:
            newest_hash = t.tx_hash
            if t.timestamp:
                newest_transfer_at = t.timestamp
            contract = (t.token_address or "").lower()
            if not contract or contract in _QUOTE_TOKENS:
                continue
            wallet_l = wallet.lower()
            is_buy = (t.to_address or "").lower() == wallet_l
            is_sell = (t.from_address or "").lower() == wallet_l
            if is_buy and not await _open_position_exists(wallet, contract):
                price = await _current_price_usd(contract)
                await _open_position(wallet, label, contract, t.token_symbol, t.tx_hash, price)
                opened += 1
            elif is_sell and await _open_position_exists(wallet, contract):
                price = await _current_price_usd(contract)
                await _close_open_position(wallet, contract, t.tx_hash, price)
                closed += 1

        if newest_hash != last_seen:
            await _set_cursor(wallet, newest_hash, last_transfer_at=newest_transfer_at)
        return ShadowScanResult(wallet, opened, closed)
    except Exception as exc:  # noqa: BLE001 -- shadow, never blocking
        logger.info("wallet_copy_shadow: scan failed for %s/%s (%s)", label, wallet[:10], exc)
        return ShadowScanResult(wallet, 0, 0, error=str(exc))


async def refresh_open_marks(wallet: str) -> None:
    """Best-effort mark-to-market refresh for this wallet's still-open shadow
    positions -- called after each scan so summary() reflects a recent
    latent P&L without a dedicated always-on price poller. Never raises:
    same doctrine as scan_wallet, a marking failure must never break the
    scan cycle for the other 7 wallets."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT DISTINCT contract FROM wallet_copy_shadow_position "
                "WHERE wallet_address = ? AND status = 'open'",
                (wallet,),
            )
            contracts = [r[0] for r in await cursor.fetchall()]
        for contract in contracts:
            price = await _current_price_usd(contract)
            if price is not None:
                await _mark_open_position(wallet, contract, price)
    except Exception as exc:  # noqa: BLE001 -- shadow, never blocking
        logger.info("wallet_copy_shadow: mark refresh failed for %s (%s)", wallet[:10], exc)


async def run_scan_cycle() -> list[ShadowScanResult]:
    """Scans all 8 tracked wallets, one at a time (sequential -- Blockscout
    is a shared, rate-limited resource, no reason to burst it). Never
    raises: a single wallet's failure is reported in its own result, the
    others still run."""
    results: list[ShadowScanResult] = []
    for wallet, meta in TRACKED_WALLETS.items():
        res = await scan_wallet(wallet, meta)
        results.append(res)
        await refresh_open_marks(wallet)
    return results


async def summary() -> dict[str, dict]:
    """Per-wallet shadow performance -- the number that answers the
    operator's question: does copying THIS wallet, alone, actually work?
    Realized P&L only counts closed positions (a real, not guessed, number);
    open positions are reported separately via their last mark, clearly
    labeled as latent/unrealized."""
    await _ensure_tables()
    now = datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    async with aiosqlite.connect(DB_PATH) as db:
        for wallet, meta in TRACKED_WALLETS.items():
            activity_row = await db.execute(
                "SELECT last_transfer_at FROM wallet_copy_shadow_cursor WHERE wallet_address = ?",
                (wallet,),
            )
            last_transfer_at = (await activity_row.fetchone() or (None,))[0]
            activity_status = "unknown"
            if last_transfer_at:
                try:
                    dt = datetime.fromisoformat(last_transfer_at.replace("Z", "+00:00"))
                    activity_status = "dormant" if (now - dt).days >= INACTIVITY_THRESHOLD_DAYS else "active"
                except (ValueError, TypeError):
                    activity_status = "unknown"

            cursor = await db.execute(
                "SELECT status, entry_price_usd, exit_price_usd, last_mark_price_usd "
                "FROM wallet_copy_shadow_position WHERE wallet_address = ?",
                (wallet,),
            )
            rows = await cursor.fetchall()
            closed_pnl_usd = 0.0
            closed_count = 0
            open_latent_pnl_usd = 0.0
            open_count = 0
            for status, entry, exit_, mark in rows:
                if not entry or entry <= 0:
                    continue
                if status == "closed" and exit_:
                    closed_pnl_usd += POSITION_SIZE_USD * (exit_ / entry - 1.0)
                    closed_count += 1
                elif status == "open":
                    open_count += 1
                    if mark:
                        open_latent_pnl_usd += POSITION_SIZE_USD * (mark / entry - 1.0)
            out[wallet] = {
                "label": meta["label"],
                "tier": meta["tier"],
                "evidence": meta["evidence"],
                "wallet_first_tx_at": meta.get("wallet_first_tx_at"),
                "status": activity_status,
                "last_transfer_at": last_transfer_at,
                "closed_positions": closed_count,
                "realized_pnl_usd": round(closed_pnl_usd, 2),
                "open_positions": open_count,
                "unrealized_pnl_usd": round(open_latent_pnl_usd, 2),
            }
    return out
