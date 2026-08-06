"""Scalping_v9 -- fixed-watchlist RSI+MFI synchronized-oversold engine
(06/08, full operator spec, every parameter operator-provided from their own
manual simulation on the SPX 5-min chart -- never re-derived or "improved"
here without an explicit operator decision):

  - WATCHLIST of fixed tokens (SPX first; the operator will add ~4 more) --
    never the momentum discovery stream, no liquidity/volume/other floor.
    The GoPlus honeypot check stays (CLAUDE.md absolute: the one hard
    guardrail, never weakened for a new pocket).
  - TIMEFRAME: 5-minute candles ONLY (mode="scalping_5m" OHLCV ladder,
    single rung, no coarser fallback).
  - ENTRY: RSI(18) < 21 AND MFI(10) < 20 on the SAME closed candle --
    "quand les deux en même temps le sont". One candle below on its own, or
    the two dipping 2-3 candles apart, is explicitly NOT a signal. One buy
    per synchronized episode (re-arms once at least one indicator closes
    back above its limit); buy immediately on detection, no confirmation
    wait ("achat sans analyse... il faut être rapide, le signal est rare").
  - SIZING: every buy = 3% of the wallet's REMAINING cash. Positions stack
    (several concurrent SPX positions are legitimate, one per episode) --
    the allow_multiple seam in paper_trader.open_position exists for this.
  - FILL SIMULATION: buy at spot * (1 + 0.3% fee + 1% impact); sell at
    spot * (1 - 0.3% - 1%), symmetric (operator-confirmed). Modeled HERE
    explicitly -- positions are opened mode="standard" with
    pool_liquidity_usd=None so paper_trader's own scalping fee/impact
    machinery never double-counts.
  - EXIT: flat -5% trailing stop from the SPOT high-water mark, the ONLY
    exit (no TP, no overbought exit, no stagnation timeout -- operator
    choice) + the standard weekly reset (V9_WALLET rides
    all_pocket_wallets(), the heartbeat weekly loop covers it).
    The generic position-management loop explicitly SKIPS this wallet
    (see _run_paper_cycle_locked) -- this module is the single manager.

Deterministic, no LLM call. Own 5-min heartbeat cycle (scalping_v9_cycle)
-- API cost is negligible by design: 1 DexScreener pair fetch + 1
GeckoTerminal OHLCV call per watchlist token per 5 min."""
from __future__ import annotations

import logging

from aria_core import paper_trader

logger = logging.getLogger(__name__)

# Watchlist SEED -- the DB (v9_watchlist table below) is the live list the
# cycle reads; this tuple only seeds it once on first access. The operator
# manages the list themselves via Telegram (/v9add, /v9list, /v9remove --
# explicit request, 06/08: "je pourrai ajouter les contrats moi-même") --
# additions take effect on the next 5-min cycle, zero redeploy.
V9_WATCHLIST: tuple[dict, ...] = (
    {
        "contract": "0x50dA645f148798F68EF2d7dB7C1CB22A6819bb2C",
        "chain": "base",
        "symbol": "SPX",
    },
)

# Chains probed (in order) when /v9add gets a bare EVM address with no chain
# hint -- the most liquid pool across them wins. A non-0x address is tried as
# Solana directly.
_DETECTION_CHAINS = ("base", "ethereum")


# Per-token tunable settings (06/08 "go réglages", operator: adjust in real
# time to refine -- a /v9set takes effect on the NEXT 5-min cycle, no
# redeploy). NULL in DB = use the module default (the operator's original
# SPX-validated spec). Bounds are sanity rails, not strategy opinions.
_SETTING_BOUNDS: dict[str, tuple[float, float]] = {
    "rsi_period": (2, 100),
    "rsi_lower": (1, 99),
    "mfi_period": (2, 100),
    "mfi_lower": (1, 99),
    "trail_pct": (0.01, 0.50),
    # discrete, validated against ALLOWED_TIMEFRAMES (not just the range)
    "timeframe_min": (5, 60),
}
_SETTING_COLUMNS = tuple(_SETTING_BOUNDS)


def _setting_defaults() -> dict:
    return {
        "rsi_period": RSI_PERIOD,
        "rsi_lower": RSI_LOWER_LIMIT,
        "mfi_period": MFI_PERIOD,
        "mfi_lower": MFI_LOWER_LIMIT,
        "trail_pct": TRAIL_STOP_PCT,
        "timeframe_min": TIMEFRAME_MIN,
    }


async def _ensure_watchlist_table() -> None:
    """Creates the table and seeds V9_WATCHLIST once -- INSERT OR IGNORE, so
    an operator /v9remove of a seeded token is never resurrected. Settings
    columns soft-migrated (ALTER ... ADD COLUMN, same pattern as
    codex_request_log's mode column) -- a pre-settings row keeps NULLs and
    falls back to the module defaults."""
    import aiosqlite

    async with aiosqlite.connect(paper_trader.DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS v9_watchlist (
                contract TEXT PRIMARY KEY,
                chain TEXT NOT NULL,
                symbol TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL,
                removed_at TEXT
            )
            """
        )
        for column in _SETTING_COLUMNS:
            try:
                await db.execute(f"ALTER TABLE v9_watchlist ADD COLUMN {column} REAL")
            except aiosqlite.OperationalError:
                pass  # column already exists -- soft migration, idempotent
        for token in V9_WATCHLIST:
            await db.execute(
                "INSERT OR IGNORE INTO v9_watchlist (contract, chain, symbol, active, added_at) "
                "VALUES (?, ?, ?, 1, datetime('now'))",
                (token["contract"].lower(), token["chain"], token["symbol"]),
            )
        await db.commit()


async def get_watchlist() -> list[dict]:
    """Active watchlist, the one the 5-min cycle iterates -- per-token
    settings resolved (DB value or module default, never None)."""
    import aiosqlite

    await _ensure_watchlist_table()
    settings_cols = ", ".join(_SETTING_COLUMNS)
    async with aiosqlite.connect(paper_trader.DB_PATH) as db:
        cur = await db.execute(
            f"SELECT contract, chain, symbol, {settings_cols} "
            "FROM v9_watchlist WHERE active = 1 ORDER BY added_at"
        )
        rows = await cur.fetchall()
    defaults = _setting_defaults()
    out: list[dict] = []
    for row in rows:
        entry = {"contract": row[0], "chain": row[1], "symbol": row[2]}
        for i, column in enumerate(_SETTING_COLUMNS):
            value = row[3 + i]
            entry[column] = defaults[column] if value is None else float(value)
        entry["rsi_period"] = int(entry["rsi_period"])
        entry["mfi_period"] = int(entry["mfi_period"])
        entry["timeframe_min"] = int(entry["timeframe_min"])
        out.append(entry)
    return out


async def set_watchlist_settings(contract: str, **settings: float) -> tuple[dict | None, str]:
    """Real-time per-token tuning (/v9set): validates against
    ``_SETTING_BOUNDS`` then persists -- effective on the next 5-min cycle.
    Returns ``(resolved entry, "")`` or ``(None, reason)``. Only the keys
    passed change; the others keep their current value (or default)."""
    import aiosqlite

    contract_lower = (contract or "").strip().lower()
    if not settings:
        return None, "aucun réglage fourni"
    for key, value in settings.items():
        if key not in _SETTING_BOUNDS:
            return None, f"réglage inconnu : {key}"
        if key == "timeframe_min":
            if int(value) not in ALLOWED_TIMEFRAMES:
                allowed = "/".join(str(t) for t in ALLOWED_TIMEFRAMES)
                return None, f"timeframe {value} non supportée (choix : {allowed} min)"
            continue
        low, high = _SETTING_BOUNDS[key]
        if not (low <= float(value) <= high):
            return None, f"{key}={value} hors bornes [{low}-{high}]"
    await _ensure_watchlist_table()
    assignments = ", ".join(f"{key} = ?" for key in settings)
    async with aiosqlite.connect(paper_trader.DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE v9_watchlist SET {assignments} WHERE contract = ? AND active = 1",
            (*[float(v) for v in settings.values()], contract_lower),
        )
        await db.commit()
        if cur.rowcount == 0:
            return None, "contrat absent de la watchlist active (/v9list)"
    for token in await get_watchlist():
        if token["contract"] == contract_lower:
            return token, ""
    return None, "contrat absent de la watchlist active (/v9list)"


async def add_watchlist_token(contract: str, chain: str | None = None) -> tuple[dict | None, str]:
    """Resolves the token's most liquid pool (DexScreener) then activates it
    in the watchlist. Returns ``(entry, "")`` or ``(None, reason)`` -- the
    Telegram handler relays the reason verbatim, never a silent failure.
    Chain auto-detected when not provided: EVM (0x...) probes
    ``_DETECTION_CHAINS``, anything else is tried as Solana."""
    import aiosqlite

    contract = (contract or "").strip()
    if not contract:
        return None, "adresse vide"
    chains = [chain] if chain else (
        list(_DETECTION_CHAINS) if contract.lower().startswith("0x") else ["solana"]
    )
    best_pair, best_chain = None, None
    for candidate_chain in chains:
        try:
            pair = await paper_trader._default_pair_lookup(contract, chain=candidate_chain)
        except Exception as exc:  # noqa: BLE001 -- one chain's failure never hides another's pool
            logger.info("scalping_v9: /v9add lookup %s on %s failed (%s)", contract[:10], candidate_chain, exc)
            continue
        if pair is not None and pair.price_usd and pair.price_usd > 0:
            if best_pair is None or pair.liquidity_usd > best_pair.liquidity_usd:
                best_pair, best_chain = pair, candidate_chain
    if best_pair is None:
        return None, (
            f"aucun pool liquide trouvé pour {contract[:14]}… sur "
            f"{'/'.join(chains)} (DexScreener)"
        )
    entry = {
        "contract": contract.lower(),
        "chain": best_chain,
        "symbol": best_pair.base_symbol or contract[:8],
    }
    await _ensure_watchlist_table()
    async with aiosqlite.connect(paper_trader.DB_PATH) as db:
        await db.execute(
            "INSERT INTO v9_watchlist (contract, chain, symbol, active, added_at) "
            "VALUES (?, ?, ?, 1, datetime('now')) "
            "ON CONFLICT(contract) DO UPDATE SET active = 1, chain = excluded.chain, "
            "symbol = excluded.symbol, removed_at = NULL",
            (entry["contract"], entry["chain"], entry["symbol"]),
        )
        await db.commit()
    entry["liquidity_usd"] = best_pair.liquidity_usd
    entry["price_usd"] = best_pair.price_usd
    return entry, ""


async def remove_watchlist_token(contract: str) -> bool:
    """Deactivates (never deletes -- history stays queryable). Open positions
    on the token keep being managed to natural close by the cycle's
    management pass, which iterates POSITIONS, not the watchlist."""
    import aiosqlite

    await _ensure_watchlist_table()
    async with aiosqlite.connect(paper_trader.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE v9_watchlist SET active = 0, removed_at = datetime('now') "
            "WHERE contract = ? AND active = 1",
            ((contract or "").strip().lower(),),
        )
        await db.commit()
        return cur.rowcount > 0

# Operator-charted indicator settings (TradingView screenshots, 06/08):
# RSI Length 18 (raw value -- the SMA smoothing line is unchecked on the
# chart, so the smoothed series is deliberately NOT what the limit applies
# to), LowerLimit 21. MFI Length 10, LowerLimit 20.
RSI_PERIOD = 18
RSI_LOWER_LIMIT = 21.0
MFI_PERIOD = 10
MFI_LOWER_LIMIT = 20.0

BUY_PCT_OF_REMAINING_CASH = 0.03
TRAIL_STOP_PCT = 0.05
# Per-token candle timeframe (minutes) -- 5 by default (the operator's
# original SPX chart), /v9set tf=15/30/60 switches it. Discrete values only:
# each maps to a dedicated single-rung OHLCV ladder (services/ohlcv.py).
TIMEFRAME_MIN = 5
ALLOWED_TIMEFRAMES = (5, 15, 30, 60)
# "le prix d'achat c'est le prix net + 0.3% + 1% pour simuler le price
# impact" -- 0.3% pool fee + 1% impact, applied symmetrically on the sell.
SWAP_FEE_PCT = 0.003
PRICE_IMPACT_PCT = 0.01
_TOTAL_FEE_PCT = SWAP_FEE_PCT + PRICE_IMPACT_PCT

# Warmup: RSI(18) needs 19 closes, MFI(10) needs 11 candles -- generous
# margin so a thin history degrades to "no signal" rather than a
# partially-warmed read (same doctrine as scalping_variants).
_MIN_CANDLES_FOR_SIGNAL = 40

# Don't open dust: below this allocation the paper position teaches nothing.
_MIN_ALLOC_USD = 10.0

# One-buy-per-episode dedup across cycles: the transition is detected on the
# last 1-2 closed candles (2, not 1: a cycle firing just before a candle
# close would otherwise permanently miss the transition it was about to
# see). In-memory marker of the last bought episode per contract (transition
# candle ts); belt-and-suspenders on restart: a fresh open position less
# than _EPISODE_GUARD_SECONDS old on the same contract also blocks a re-buy.
_last_buy_episode_ts: dict[str, float] = {}
_EPISODE_GUARD_SECONDS = 15 * 60.0


def _both_below(
    rsi_v: float | None, mfi_v: float | None,
    *, rsi_lower: float = RSI_LOWER_LIMIT, mfi_lower: float = MFI_LOWER_LIMIT,
) -> bool | None:
    """None while either indicator is still warming up -- never a guess."""
    if rsi_v is None or mfi_v is None:
        return None
    return rsi_v < rsi_lower and mfi_v < mfi_lower


def _find_entry_transition(
    rsi: list, mfi: list,
    *, rsi_lower: float = RSI_LOWER_LIMIT, mfi_lower: float = MFI_LOWER_LIMIT,
) -> int | None:
    """Index (into the candle list) of a fresh synchronized-oversold
    TRANSITION on one of the last 2 closed candles: both below now, NOT both
    below on the candle before. Returns None when there is no fresh episode
    -- including the both-below-for-a-while case (episode already bought or
    already stale, per the one-buy-per-episode spec)."""
    n = len(rsi)
    for idx in (n - 1, n - 2):
        if idx < 1:
            continue
        now = _both_below(rsi[idx], mfi[idx], rsi_lower=rsi_lower, mfi_lower=mfi_lower)
        prev = _both_below(rsi[idx - 1], mfi[idx - 1], rsi_lower=rsi_lower, mfi_lower=mfi_lower)
        if now is True and prev is False:
            # a transition at n-2 only counts if the episode is still live
            # on the last candle (still both below) -- "be fast" never means
            # buying an episode that already ended.
            if idx == n - 2 and _both_below(
                rsi[-1], mfi[-1], rsi_lower=rsi_lower, mfi_lower=mfi_lower,
            ) is not True:
                return None
            return idx
    return None


def _degraded_buy_price(spot: float) -> float:
    return spot * (1.0 + _TOTAL_FEE_PCT)


def _degraded_sell_price(spot: float) -> float:
    return spot * (1.0 - _TOTAL_FEE_PCT)


async def _manage_positions(
    contract: str, spot: float, notifier=None, *, trail_pct: float = TRAIL_STOP_PCT,
) -> list[dict]:
    """Flat trailing stop (per-token %, default -5%) from the SPOT
    high-water mark -- the pocket's ONLY exit. High water lives on SPOT
    (chart) prices, never the degraded fill (same doctrine as the 08/05
    high-water-vs-fill fix in paper_trader)."""
    closed: list[dict] = []
    contract_lower = contract.lower()
    for p in await paper_trader.get_open_positions(wallet=paper_trader.V9_WALLET):
        if (p.get("contract") or "").lower() != contract_lower:
            continue
        high_water = max(p.get("high_water_price") or 0.0, spot)
        if high_water > (p.get("high_water_price") or 0.0):
            await paper_trader._update_high_water(p["id"], high_water)
        if spot <= high_water * (1.0 - trail_pct):
            result = await paper_trader.close_position(
                contract,
                _degraded_sell_price(spot),
                position_id=p["id"],
                reason=f"trailing -{trail_pct * 100:g}% (v9)",
                notes=(
                    f"spot {spot:.6g} <= plus haut {high_water:.6g} -{trail_pct * 100:g}% ; "
                    f"sortie simulée -{_TOTAL_FEE_PCT * 100:.1f}% (frais+impact)"
                ),
            )
            if result:
                closed.append(result)
                if notifier is not None:
                    try:
                        await notifier(paper_trader.format_sell_alert(result))
                    except Exception as exc:  # noqa: BLE001 -- alert must never block the cycle
                        logger.info("scalping_v9: sell alert failed (%s)", exc)
    return closed


async def _recent_position_guard(contract: str) -> bool:
    """True when an open v9 position on this contract is younger than the
    episode guard -- restart-safe belt against a double buy on the SAME
    still-running episode (the in-memory marker dies with the process)."""
    from datetime import datetime, timezone

    contract_lower = contract.lower()
    for p in await paper_trader.get_open_positions(wallet=paper_trader.V9_WALLET):
        if (p.get("contract") or "").lower() != contract_lower:
            continue
        opened_at = p.get("opened_at")
        if not opened_at:
            continue
        try:
            opened = datetime.fromisoformat(opened_at)
            age = (datetime.now(timezone.utc) - opened).total_seconds()
        except ValueError:
            continue
        if age < _EPISODE_GUARD_SECONDS:
            return True
    return False


async def run_v9_cycle(*, notifier=None) -> dict:
    """One full pass over the watchlist: manage open positions (trailing)
    then evaluate the entry signal. Called by the heartbeat every 5 min
    (scalping_v9_cycle); double-gated there (ARIA_PAPER_TRADING_ENABLED +
    ARIA_SCALPING_V9_ENABLED), re-checked here (defence in depth)."""
    from aria_core import paper_pause

    actions: dict = {"opened": [], "closed": [], "checked": 0, "holds": []}
    if not paper_trader.scalping_v9_enabled() or paper_pause.is_paused():
        return actions

    from aria_core import momentum_entry
    from aria_core.services.geckoterminal import geckoterminal_client
    from aria_core.skills import indicators
    from aria_core.skills.entry_signals import rsi_series

    for token in await get_watchlist():
        contract, chain, label = token["contract"], token["chain"], token["symbol"]
        actions["checked"] += 1
        try:
            pair = await paper_trader._default_pair_lookup(contract, chain=chain)
        except Exception as exc:  # noqa: BLE001 -- one token's failure never blocks the rest
            logger.info("scalping_v9[%s]: pair lookup failed (%s)", label, exc)
            continue
        if pair is None or not pair.price_usd or pair.price_usd <= 0:
            actions["holds"].append({"symbol": label, "reason": "no_liquid_pair"})
            continue
        spot = pair.price_usd

        actions["closed"].extend(
            await _manage_positions(
                contract, spot, notifier=notifier, trail_pct=token["trail_pct"],
            )
        )

        try:
            ohlcv = await geckoterminal_client.get_ohlcv(
                pair.pair_address, network=chain,
                mode=f"scalping_{token['timeframe_min']}m",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("scalping_v9[%s]: OHLCV failed (%s)", label, exc)
            continue
        if not ohlcv.available or not ohlcv.candles:
            actions["holds"].append({"symbol": label, "reason": "ohlcv_unavailable"})
            continue
        # Last candle is the one still forming (standard real-time OHLCV
        # behavior) -- never compute the signal on an unclosed candle, same
        # centralized-trim doctrine as scalping_variants.
        candles = ohlcv.candles[:-1]
        if len(candles) < _MIN_CANDLES_FOR_SIGNAL:
            actions["holds"].append({"symbol": label, "reason": "insufficient_candles"})
            continue

        rsi = rsi_series([c.close for c in candles], period=token["rsi_period"])
        mfi = indicators.mfi_series(candles, period=token["mfi_period"])
        transition_idx = _find_entry_transition(
            rsi, mfi, rsi_lower=token["rsi_lower"], mfi_lower=token["mfi_lower"],
        )
        if transition_idx is None:
            actions["holds"].append({"symbol": label, "reason": "no_signal"})
            continue

        transition_ts = float(getattr(candles[transition_idx], "ts", 0) or 0)
        if _last_buy_episode_ts.get(contract.lower()) == transition_ts:
            actions["holds"].append({"symbol": label, "reason": "episode_already_bought"})
            continue
        if await _recent_position_guard(contract):
            actions["holds"].append({"symbol": label, "reason": "episode_guard_recent_position"})
            continue

        # The one hard guardrail (CLAUDE.md absolute) -- fail-closed.
        clear, hp_reason, _hp_code = await momentum_entry._check_honeypot(
            contract, chain, liquidity_usd=pair.liquidity_usd,
        )
        if not clear:
            actions["holds"].append({"symbol": label, "reason": f"honeypot:{hp_reason}"})
            logger.info("scalping_v9[%s]: honeypot gate refused (%s)", label, hp_reason)
            continue

        cash = await paper_trader.cash_available(wallet=paper_trader.V9_WALLET)
        alloc = cash * BUY_PCT_OF_REMAINING_CASH
        if alloc < _MIN_ALLOC_USD:
            actions["holds"].append({"symbol": label, "reason": "insufficient_cash"})
            continue

        rsi_shown = rsi[transition_idx]
        mfi_shown = mfi[transition_idx]
        pos = await paper_trader.open_position(
            contract,
            pair.base_symbol or label,
            _degraded_buy_price(spot),
            wallet=paper_trader.V9_WALLET,
            alloc_usd=alloc,
            invalidation_price=spot * (1.0 - token["trail_pct"]),
            chain=chain,
            mode="standard",
            strategy="momentum",
            discovery_channel="v9_watchlist",
            entry_market_cap_usd=pair.market_cap_usd,
            allow_multiple=True,
            thesis=(
                f"[V9] Survente synchronisée sur bougie {token['timeframe_min']}min : "
                f"RSI({token['rsi_period']})={rsi_shown:.1f} < {token['rsi_lower']:g} "
                f"ET MFI({token['mfi_period']})={mfi_shown:.1f} < {token['mfi_lower']:g} "
                f"en même temps. Achat immédiat 3% du cash restant, sortie unique : "
                f"stop suiveur -{token['trail_pct'] * 100:g}% du plus haut spot."
            ),
        )
        if pos is None:
            actions["holds"].append({"symbol": label, "reason": "open_refused"})
            continue
        # Trailing runs on SPOT ("prix net réel sur le graphique") -- reseed
        # the high-water at spot, not the degraded fill open_position stored.
        await paper_trader._update_high_water(pos["id"], spot)
        _last_buy_episode_ts[contract.lower()] = transition_ts
        actions["opened"].append(pos)
        if notifier is not None:
            try:
                await notifier(paper_trader.format_buy_alert(pos))
            except Exception as exc:  # noqa: BLE001 -- alert must never block the cycle
                logger.info("scalping_v9: buy alert failed (%s)", exc)

    return actions
