"""Narrative-signal shadow -- forward-tests "trade the news, not the chart".

08/08, operator direction ("enfaite elle doit trader le bruit et pas le
graphique" then "tu dois debloquer les info pour aria") after a 4-token
exercise (CLANKER/MAMO/WIRE/aeon) showed every real pump had an EVENT cause
(Farcaster acquisition, Coinbase listing, Base-app integration) that ARIA's
technical-only entry gates never see. Counter-evidence from the same session:
narrative alone is fabricable (gitlawb advertised a "LIVE revenue flywheel"
backed by $8k of real burns) and says WHAT, never WHEN (CLANKER's Farcaster
link was visible 11 months before its pump). So: never route a trade through
this -- observe forward, measure, then decide. Same shadow doctrine as
v8_limit_shadow.py / v8_rsi_reversal_shadow.py / combo_signal_shadow.py.

Two verifiable, hard-to-fabricate signals, ZERO network calls per candidate
(both are GLOBAL catalogs, cached module-wide and refreshed on a TTL):

1. ``defillama_revenue`` -- the candidate's token belongs to a protocol with
   real fee revenue on DefiLlama (the one criterion that separated every
   real mechanism from marketing in the 08/08 session: clanker $274k/30d vs
   gitlawb ~$8k total). Address match (strong) via /protocols' ``address``
   field; symbol match (weak, explicitly tagged) as fallback -- only ~half
   of DefiLlama protocols carry a token address (verified live: 4134/8002).

2. ``cex_listing_phase`` -- the candidate's symbol just APPEARED in
   Coinbase Exchange's public /products catalog (persisted diff), or sits
   in a launch phase (``post_only``/``limit_only`` -- Coinbase ships
   listings in phases, verified live: 21 products currently mid-phase).
   This is the exact MAMO signal (+42% BEFORE the official trading hour)
   that nothing in the pipeline could see.

Forward measurement: each signal row records the price at signal time; later
evaluations of the same contract fill ``price_after_24h``/``price_after_7d``
once enough time has passed. The question the data answers: do
narrative-signal candidates outperform the baseline forward -- BEFORE any
strategy change is proposed on top.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import aiosqlite
import httpx

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

_DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"
_DEFILLAMA_FEES_URL = (
    "https://api.llama.fi/overview/fees/base"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
)
_COINBASE_PRODUCTS_URL = "https://api.exchange.coinbase.com/products"

# Catalog TTLs -- global lists, not per-candidate lookups. DefiLlama's
# protocol registry moves slowly (6h is generous); Coinbase's catalog is the
# time-sensitive one (a listing phase lasts hours), refreshed hourly.
_DEFILLAMA_TTL_SECONDS = 6 * 3600.0
_COINBASE_TTL_SECONDS = 3600.0

# A protocol only counts as "real revenue" above this floor -- calibrated on
# the 08/08 session's real comparisons: gitlawb's ENTIRE cumulative burn was
# ~$8k (marketing tier), while every genuine mechanism (clanker $274k/30d,
# Aerodrome $5.2M/30d, RAY) clears $10k/30d by an order of magnitude.
MIN_REVENUE_30D_USD = 10_000.0

# One signal row per (contract, signal_type) per this window -- a catalog
# that keeps matching must not re-log the same discovery every evaluation.
_DEDUP_WINDOW_DAYS = 7.0

SIGNAL_DEFILLAMA_REVENUE = "defillama_revenue"
SIGNAL_CEX_LISTING_PHASE = "cex_listing_phase"
# Generic third detector (08/08, operator: "tous signaux suffisamment
# importants sur n'importe quel token, pas juste coinbase ou defillama") --
# conviction_research's per-candidate diligence (X buzz, website, GitHub,
# Farcaster, Telegram, Tavily) logged as a dated signal whenever it scores
# high. Honest limit, documented where it's hooked: conviction_research only
# runs on candidates that already reached a technical BUY (standard mode) --
# an aeon-at-the-ATL (active GitHub, no technical setup) still never reaches
# it; mass pre-BUY coverage costs real X/Tavily budget per candidate and is
# a separate chantier.
SIGNAL_CONVICTION_RESEARCH = "conviction_research"
# conviction_research scores on a 0-10 scale (see its potential_score).
MIN_CONVICTION_SCORE = 7.0

# (fetched_at_monotonic, payload) -- module-level caches, one per catalog.
_defillama_cache: tuple[float, dict[str, dict], dict[str, dict]] | None = None
_coinbase_cache: tuple[float, dict[str, dict]] | None = None
_fetch_lock = asyncio.Lock()

_table_ready = False


async def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS narrative_signal_shadow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                symbol TEXT,
                signal_type TEXT NOT NULL,
                signal_detail TEXT,
                match_strength TEXT,
                price_at_signal REAL,
                observed_at TEXT NOT NULL,
                price_after_24h REAL,
                price_after_7d REAL
            )
            """
        )
        await db.commit()
    _table_ready = True


async def _fetch_json(url: str, *, timeout: float = 20.0) -> object | None:
    """One attempt, no retry -- a catalog refresh that fails just leaves the
    previous cache in place (or skips the signal entirely), never blocks the
    evaluation path and never amplifies pressure on a saturated provider
    (same 08/08 lesson as the GeckoTerminal no-retry fix)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            logger.info("narrative_shadow: HTTP %s on %s", resp.status_code, url)
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001 -- never blocking
        logger.info("narrative_shadow: fetch %s failed (%s)", url, exc)
        return None


async def _refresh_defillama() -> tuple[dict[str, dict], dict[str, dict]] | None:
    """(by_address, by_symbol) of Base protocols with real 30d fees.
    by_address keys are lowercase token addresses (strong match); by_symbol
    keys are UPPERCASE symbols (weak match, several tokens can share one)."""
    protocols = await _fetch_json(_DEFILLAMA_PROTOCOLS_URL)
    fees = await _fetch_json(_DEFILLAMA_FEES_URL)
    if not isinstance(protocols, list) or not isinstance(fees, dict):
        return None

    fees_by_slug: dict[str, float] = {}
    for item in fees.get("protocols", []) or []:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        total30d = item.get("total30d")
        if slug and isinstance(total30d, (int, float)) and total30d >= MIN_REVENUE_30D_USD:
            fees_by_slug[str(slug)] = float(total30d)

    by_address: dict[str, dict] = {}
    by_symbol: dict[str, dict] = {}
    for proto in protocols:
        if not isinstance(proto, dict):
            continue
        slug = str(proto.get("slug") or "")
        if slug not in fees_by_slug:
            continue
        entry = {
            "slug": slug,
            "name": proto.get("name"),
            "revenue_30d": fees_by_slug[slug],
        }
        address = proto.get("address")
        if isinstance(address, str) and address.startswith("0x"):
            by_address[address.lower()] = entry
        symbol = proto.get("symbol")
        if isinstance(symbol, str) and symbol and symbol != "-":
            by_symbol[symbol.upper()] = entry
    return by_address, by_symbol


async def _refresh_coinbase() -> dict[str, dict] | None:
    """UPPERCASE base symbol -> product info for every non-delisted Coinbase
    product. The interesting states: newly-appeared symbols (vs the persisted
    set in DB) and launch phases (post_only/limit_only)."""
    products = await _fetch_json(_COINBASE_PRODUCTS_URL)
    if not isinstance(products, list):
        return None
    by_symbol: dict[str, dict] = {}
    for p in products:
        if not isinstance(p, dict) or p.get("status") != "online":
            continue
        base = str(p.get("base_currency") or "").upper()
        if not base:
            continue
        launch_phase = bool(p.get("post_only")) or bool(p.get("limit_only"))
        existing = by_symbol.get(base)
        # A symbol counts as "in launch phase" only if EVERY product is --
        # SNX-GBP being limit_only while SNX-USD trades freely is a
        # jurisdiction quirk, not a new listing.
        if existing is None:
            by_symbol[base] = {"launch_phase": launch_phase, "product_ids": [p.get("id")]}
        else:
            existing["launch_phase"] = existing["launch_phase"] and launch_phase
            existing["product_ids"].append(p.get("id"))
    return by_symbol


async def _get_catalogs() -> tuple[
    dict[str, dict] | None, dict[str, dict] | None, dict[str, dict] | None,
]:
    """(defillama_by_address, defillama_by_symbol, coinbase_by_symbol) --
    each None when its catalog is unavailable AND no previous cache exists
    (the signal is skipped, never guessed)."""
    global _defillama_cache, _coinbase_cache
    now = time.monotonic()
    async with _fetch_lock:
        if _defillama_cache is None or (now - _defillama_cache[0]) >= _DEFILLAMA_TTL_SECONDS:
            refreshed = await _refresh_defillama()
            if refreshed is not None:
                _defillama_cache = (now, refreshed[0], refreshed[1])
        if _coinbase_cache is None or (now - _coinbase_cache[0]) >= _COINBASE_TTL_SECONDS:
            refreshed_cb = await _refresh_coinbase()
            if refreshed_cb is not None:
                _coinbase_cache = (now, refreshed_cb)
    dl_addr = _defillama_cache[1] if _defillama_cache else None
    dl_sym = _defillama_cache[2] if _defillama_cache else None
    cb = _coinbase_cache[1] if _coinbase_cache else None
    return dl_addr, dl_sym, cb


_CEX_SEEN_DDL = (
    "CREATE TABLE IF NOT EXISTS narrative_shadow_cex_seen ("
    "symbol TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL)"
)


async def _known_coinbase_symbols() -> set[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CEX_SEEN_DDL)
        await db.commit()
        cursor = await db.execute("SELECT symbol FROM narrative_shadow_cex_seen")
        rows = await cursor.fetchall()
    return {r[0] for r in rows}


async def _mark_coinbase_symbols_seen(symbols: set[str]) -> None:
    if not symbols:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CEX_SEEN_DDL)
        await db.executemany(
            "INSERT OR IGNORE INTO narrative_shadow_cex_seen (symbol, first_seen_at) "
            "VALUES (?, datetime('now'))",
            [(s,) for s in symbols],
        )
        await db.commit()


async def _recent_signal_exists(contract: str, chain: str, signal_type: str) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_DEDUP_WINDOW_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM narrative_signal_shadow "
            "WHERE contract = ? AND chain = ? AND signal_type = ? AND observed_at >= ? LIMIT 1",
            (contract, chain, signal_type, cutoff),
        )
        return (await cursor.fetchone()) is not None


async def _update_forward_prices(contract: str, chain: str, price_usd: float) -> None:
    """Fill price_after_24h / price_after_7d on this contract's open rows
    once enough wall-clock time has passed -- driven by later evaluations of
    the same contract (no dedicated cron, same passive pattern as the v8
    shadows)."""
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, observed_at, price_after_24h, price_after_7d "
            "FROM narrative_signal_shadow "
            "WHERE contract = ? AND chain = ? AND (price_after_24h IS NULL OR price_after_7d IS NULL)",
            (contract, chain),
        )
        rows = await cursor.fetchall()
        for row_id, observed_at, after_24h, after_7d in rows:
            try:
                observed = datetime.fromisoformat(str(observed_at))
            except ValueError:
                continue
            age = now - observed
            if after_24h is None and age >= timedelta(hours=24):
                await db.execute(
                    "UPDATE narrative_signal_shadow SET price_after_24h = ? WHERE id = ?",
                    (price_usd, row_id),
                )
            if after_7d is None and age >= timedelta(days=7):
                await db.execute(
                    "UPDATE narrative_signal_shadow SET price_after_7d = ? WHERE id = ?",
                    (price_usd, row_id),
                )
        await db.commit()


async def record_evaluation(
    contract: str, chain: str, *, symbol: str | None = None, price_usd: float | None = None,
) -> None:
    """Called (best-effort) on every candidate evaluation that has a pair
    snapshot in hand. Zero per-candidate network calls -- catalog lookups
    only. Never raises."""
    try:
        await _ensure_table()
        contract_l = (contract or "").lower()
        symbol_u = (symbol or "").upper()
        if not contract_l:
            return

        if price_usd is not None and price_usd > 0:
            await _update_forward_prices(contract_l, chain, price_usd)

        dl_addr, dl_sym, cb = await _get_catalogs()

        # Signal 1 -- real protocol revenue (DefiLlama).
        match, strength = None, None
        if dl_addr is not None and contract_l in dl_addr:
            match, strength = dl_addr[contract_l], "address"
        elif dl_sym is not None and symbol_u and symbol_u in dl_sym:
            match, strength = dl_sym[symbol_u], "symbol_only"
        if match is not None and not await _recent_signal_exists(
            contract_l, chain, SIGNAL_DEFILLAMA_REVENUE,
        ):
            await _insert_signal(
                contract_l, chain, symbol_u or None, SIGNAL_DEFILLAMA_REVENUE,
                f"{match['slug']}: ${match['revenue_30d']:,.0f} fees/30d",
                strength, price_usd,
            )

        # Signal 2 -- Coinbase listing (new symbol in catalog, or launch phase).
        if cb is not None and symbol_u and symbol_u in cb:
            known = await _known_coinbase_symbols()
            is_new = symbol_u not in known
            in_phase = bool(cb[symbol_u].get("launch_phase"))
            # First run ever: the whole catalog is "new" -- seed silently,
            # only symbols appearing AFTER the seed count as a signal.
            if not known:
                await _mark_coinbase_symbols_seen(set(cb.keys()))
            elif (is_new or in_phase) and not await _recent_signal_exists(
                contract_l, chain, SIGNAL_CEX_LISTING_PHASE,
            ):
                detail = "newly listed on Coinbase" if is_new else "Coinbase launch phase (post/limit-only)"
                await _insert_signal(
                    contract_l, chain, symbol_u, SIGNAL_CEX_LISTING_PHASE,
                    detail, "symbol_only", price_usd,
                )
                if is_new:
                    await _mark_coinbase_symbols_seen({symbol_u})
    except Exception as exc:  # noqa: BLE001 -- shadow, never blocks the real path
        logger.info("narrative_shadow: record_evaluation failed for %s (%s)", contract, exc)


async def record_external_signal(
    contract: str, chain: str, *, symbol: str | None, signal_type: str,
    detail: str, price_usd: float | None,
) -> None:
    """Generic entry point for detectors that live OUTSIDE this module's
    catalog lookups (first user: momentum_entry's conviction_research hook).
    Same dedup window and best-effort doctrine as record_evaluation."""
    try:
        await _ensure_table()
        contract_l = (contract or "").lower()
        if not contract_l:
            return
        if await _recent_signal_exists(contract_l, chain, signal_type):
            return
        await _insert_signal(
            contract_l, chain, (symbol or "").upper() or None, signal_type,
            detail, "direct", price_usd,
        )
    except Exception as exc:  # noqa: BLE001 -- shadow only
        logger.info("narrative_shadow: record_external_signal failed for %s (%s)", contract, exc)


async def _insert_signal(
    contract: str, chain: str, symbol: str | None, signal_type: str,
    detail: str, strength: str | None, price_usd: float | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO narrative_signal_shadow "
            "(contract, chain, symbol, signal_type, signal_detail, match_strength, "
            " price_at_signal, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                contract, chain, symbol, signal_type, detail, strength,
                price_usd, datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    logger.info(
        "narrative_shadow: %s signal for %s/%s (%s) -- %s",
        signal_type, chain, contract[:10], strength, detail,
    )


async def summary() -> dict:
    """Per-signal-type forward performance -- the number that decides whether
    'trade the news' graduates beyond a shadow."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT signal_type, COUNT(*),
                   SUM(CASE WHEN price_after_24h IS NOT NULL THEN 1 ELSE 0 END),
                   AVG(CASE WHEN price_after_24h IS NOT NULL AND price_at_signal > 0
                       THEN (price_after_24h / price_at_signal - 1.0) * 100 END),
                   SUM(CASE WHEN price_after_7d IS NOT NULL THEN 1 ELSE 0 END),
                   AVG(CASE WHEN price_after_7d IS NOT NULL AND price_at_signal > 0
                       THEN (price_after_7d / price_at_signal - 1.0) * 100 END)
            FROM narrative_signal_shadow GROUP BY signal_type
            """
        )
        rows = await cursor.fetchall()
    return {
        row[0]: {
            "signals": row[1],
            "resolved_24h": row[2], "avg_return_24h_pct": row[3],
            "resolved_7d": row[4], "avg_return_7d_pct": row[5],
        }
        for row in rows
    }
