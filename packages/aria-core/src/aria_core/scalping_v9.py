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
  - SIZING: every buy = 3% of the wallet's REMAINING cash, capped to never
    exceed risk_guard.MAX_ALLOC_PCT_OF_POOL_LIQUIDITY (1%) of the pool's
    real liquidity (07/08, operator request -- v8 already had this, v9
    didn't). Applied by calling risk_guard.cap_alloc_to_pool_share directly
    on our own ``alloc``, BEFORE open_position -- never by passing
    pool_liquidity_usd to open_position itself (see FILL SIMULATION below
    for why). Positions stack (several concurrent SPX positions are
    legitimate, one per episode) -- the allow_multiple seam in
    paper_trader.open_position exists for this.
  - FILL SIMULATION: buy at spot * (1 + 0.3% fee + 1% impact); sell at
    spot * (1 - 0.3% - 1%), symmetric (operator-confirmed). Modeled HERE
    explicitly -- positions are opened mode="standard" with
    pool_liquidity_usd=None (open_position's own parameter, distinct from
    the sizing cap above) so paper_trader's own
    risk_guard.simulated_fill_price never re-derives a SECOND, different
    price-impact degradation on top of this module's own fixed 1%: that
    function reads the exact same pool_liquidity_usd argument as the
    sizing cap, so there is no way to opt into one without the other from
    open_position's own parameter -- confirmed by trying it (0.11% price
    drift, real double count, reverted in favor of the direct
    cap_alloc_to_pool_share call above).
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
from dataclasses import replace

from aria_core import paper_trader, risk_guard, signal_conditions

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
        # 07/08 -- configurable condition spec (signal_conditions). TEXT, not
        # REAL, hence its own migration line. NULL on every pre-existing row:
        # resolved to the rsi/mfi columns below so an untouched token keeps
        # behaving exactly as before, never silently re-armed on defaults.
        try:
            await db.execute("ALTER TABLE v9_watchlist ADD COLUMN signals TEXT")
        except aiosqlite.OperationalError:
            pass
        for token in V9_WATCHLIST:
            await db.execute(
                "INSERT OR IGNORE INTO v9_watchlist (contract, chain, symbol, active, added_at) "
                "VALUES (?, ?, ?, 1, datetime('now'))",
                (token["contract"].lower(), token["chain"], token["symbol"]),
            )
        await db.commit()


# 06/08 -- operator request ("je veut tout savoir si un jour on doit
# comprendre ce qui a fonctionné ou non"): trace EVERY cycle evaluation for
# EVERY watchlist token, not just the ones that end in a trade -- a HOLD
# cycle running on a degraded (fallback) OHLCV read is exactly the kind of
# thing that's invisible today and needs to be reconstructible after the
# fact. Append-only, never pruned automatically (small volume: <=5 tokens *
# 12 cycles/hour).
async def _ensure_cycle_log_table() -> None:
    import aiosqlite

    async with aiosqlite.connect(paper_trader.DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS v9_cycle_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                contract TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe_configured_min INTEGER,
                provider TEXT,
                timeframe_served TEXT,
                degraded INTEGER,
                rsi_last REAL,
                mfi_last REAL,
                action TEXT NOT NULL,
                reason TEXT
            )
            """
        )
        await db.commit()


async def _log_cycle_evaluation(
    *, contract: str, symbol: str, timeframe_configured_min: int,
    provenance: dict | None, rsi_last: float | None, mfi_last: float | None,
    action: str, reason: str,
) -> None:
    import aiosqlite

    provenance = provenance or {}
    async with aiosqlite.connect(paper_trader.DB_PATH) as db:
        await db.execute(
            "INSERT INTO v9_cycle_log (ts, contract, symbol, timeframe_configured_min, "
            "provider, timeframe_served, degraded, rsi_last, mfi_last, action, reason) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                contract.lower(), symbol, timeframe_configured_min,
                provenance.get("provider"), provenance.get("timeframe_served"),
                1 if provenance.get("degraded") else 0,
                rsi_last, mfi_last, action, reason,
            ),
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
            f"SELECT contract, chain, symbol, {settings_cols}, signals "
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
        stored_spec = row[3 + len(_SETTING_COLUMNS)]
        entry["signals"] = _resolve_signals(stored_spec, entry)
        # The RAW stored value, distinct from the resolved one above: only
        # this tells a caller whether the token has its OWN spec or is still
        # riding the rsi/mfi migration fallback -- the two need different
        # handling when a legacy rsi=/mfi= tweak arrives (see
        # _translate_legacy_into_spec).
        entry["_stored_signals"] = (stored_spec or "").strip() or None
        out.append(entry)
    return out


def _resolve_signals(stored: str | None, entry: dict) -> str:
    """The token's condition spec: the stored one, or -- for a row that
    predates the ``signals`` column -- the exact equivalent of its own
    rsi/mfi settings.

    Deliberately NOT ``signal_conditions.DEFAULT_SPEC`` as the fallback: a
    token the operator had tuned to, say, rsi=16/25 must keep ITS thresholds,
    never be silently reset to the module defaults by a migration."""
    if stored and stored.strip():
        return stored.strip()
    return (
        f"rsi({int(entry['rsi_period'])})<{entry['rsi_lower']:g},"
        f"mfi({int(entry['mfi_period'])})<{entry['mfi_lower']:g}"
    )


_LEGACY_SPEC_KEYS = {
    "rsi_period": ("rsi", "period"), "rsi_lower": ("rsi", "threshold"),
    "mfi_period": ("mfi", "period"), "mfi_lower": ("mfi", "threshold"),
}


async def _translate_legacy_into_spec(
    contract_lower: str, settings: dict,
) -> tuple[dict, str]:
    """Folds legacy ``rsi_*``/``mfi_*`` settings into the token's stored spec.

    No stored spec (or the caller is setting ``signals`` in the SAME command,
    which then wins outright) -> settings pass through untouched, so a token
    that has never been given a spec keeps the exact historical behaviour.

    An rsi/mfi tweak on a token whose spec no longer CONTAINS that indicator
    is refused rather than silently added: someone who moved a token to
    ``stoch<15,adx>25`` and then types ``rsi=16/25`` is far more likely to be
    mistaken than to want a third condition appended without asking."""
    legacy = {k: v for k, v in settings.items() if k in _LEGACY_SPEC_KEYS}
    if not legacy or "signals" in settings:
        return settings, ""

    stored = None
    for token in await get_watchlist():
        if token["contract"] == contract_lower:
            stored = token.get("_stored_signals")
            break
    if not stored:
        return settings, ""  # migration fallback still applies, columns are read

    conditions, error = signal_conditions.parse(stored)
    if error:
        # An unparseable stored spec is already handled fail-closed by the
        # cycle itself; never repair it silently from here.
        return settings, f"spec enregistré illisible ({error}) -- corrige-le avec signals="
    by_indicator = {c.indicator: c for c in conditions}
    for key, (indicator, field) in _LEGACY_SPEC_KEYS.items():
        if key not in legacy:
            continue
        if indicator not in by_indicator:
            present = ", ".join(c.indicator for c in conditions)
            return settings, (
                f"{indicator} n'est pas dans le signal de ce token ({present}) -- "
                f"utilise signals= pour changer les indicateurs eux-mêmes"
            )
        current = by_indicator[indicator]
        value = legacy[key]
        by_indicator[indicator] = replace(
            current,
            period=int(value) if field == "period" else current.period,
            threshold=float(value) if field == "threshold" else current.threshold,
        )
    rewritten = [by_indicator.get(c.indicator, c) for c in conditions]
    spec = signal_conditions.format_spec(rewritten)
    # Re-validated through parse: bounds are enforced by the same code path
    # as a directly-typed signals=, never a second looser check here.
    _reparsed, spec_error = signal_conditions.parse(spec)
    if spec_error:
        return settings, spec_error
    merged = {k: v for k, v in settings.items() if k not in _LEGACY_SPEC_KEYS}
    # The legacy columns stay in sync too -- they remain the source for any
    # token that later has its spec cleared, and /v9list still shows them.
    merged.update(legacy)
    merged["signals"] = spec
    return merged, ""


async def set_watchlist_settings(contract: str, **settings) -> tuple[dict | None, str]:
    """Real-time per-token tuning (/v9set): validates against
    ``_SETTING_BOUNDS`` then persists -- effective on the next 5-min cycle.
    Returns ``(resolved entry, "")`` or ``(None, reason)``. Only the keys
    passed change; the others keep their current value (or default).

    ``signals`` (07/08) is the one TEXT setting -- a condition spec parsed
    and bounds-checked by ``signal_conditions.parse`` rather than by
    ``_SETTING_BOUNDS`` (which only models numeric ranges).

    Legacy ``rsi_*``/``mfi_*`` on a token that ALREADY has a stored spec are
    translated INTO that spec rather than written to columns
    ``_resolve_signals`` would then ignore (07/08, real latent bug caught by
    the post-push architectural review and reproduced before fixing): those
    columns are only consulted as the migration fallback, so once a spec
    exists, `/v9set rsi=16/25` used to answer "réglé" while changing
    nothing -- precisely the silently-accepted-but-inert class of bug this
    same session spent the day removing elsewhere. Rewriting the spec keeps
    the two commands interchangeable, whichever order they are used in."""
    import aiosqlite

    contract_lower = (contract or "").strip().lower()
    if not settings:
        return None, "aucun réglage fourni"
    settings, translate_error = await _translate_legacy_into_spec(contract_lower, settings)
    if translate_error:
        return None, translate_error
    for key, value in settings.items():
        if key == "signals":
            _conditions, spec_error = signal_conditions.parse(str(value))
            if spec_error:
                return None, spec_error
            continue
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
            (
                *[
                    str(v) if key == "signals" else float(v)
                    for key, v in settings.items()
                ],
                contract_lower,
            ),
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
    already stale, per the one-buy-per-episode spec).

    Kept as the RSI+MFI-specific entry point (07/08): the cycle now goes
    through ``_find_transition_in_verdicts`` with a configurable condition
    spec, but this signature is the one the operator's own spec was written
    against and stays covered by its own tests."""
    return _find_transition_in_verdicts([
        _both_below(rsi[i], mfi[i], rsi_lower=rsi_lower, mfi_lower=mfi_lower)
        for i in range(len(rsi))
    ])


def _find_transition_in_verdicts(verdicts: list) -> int | None:
    """Same freshness rule as above, on an already-computed per-candle
    verdict series (True = every configured condition holds, False = at
    least one does not, None = still warming up).

    07/08 -- generalized so a token can be configured with ANY combination
    of indicators (signal_conditions.evaluate produces the series) instead
    of the hard-wired RSI+MFI pair. The transition semantics are unchanged
    and deliberately so: a fresh episode is True now / False on the candle
    before -- a None (warm-up) before never counts as a transition."""
    n = len(verdicts)
    for idx in (n - 1, n - 2):
        if idx < 1:
            continue
        if verdicts[idx] is True and verdicts[idx - 1] is False:
            # a transition at n-2 only counts if the episode is still live
            # on the last candle -- "be fast" never means buying an episode
            # that already ended.
            if idx == n - 2 and verdicts[-1] is not True:
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
    from aria_core.skills import indicators
    from aria_core.skills.entry_signals import rsi_series

    await _ensure_cycle_log_table()

    for token in await get_watchlist():
        contract, chain, label = token["contract"], token["chain"], token["symbol"]
        tf_min = token["timeframe_min"]
        actions["checked"] += 1

        async def _log(action: str, reason: str, *, rsi_last=None, mfi_last=None, provenance=None) -> None:
            # 06/08 -- operator request, full traceability: EVERY evaluation
            # of EVERY token, not just the ones that end in a trade, so a
            # cycle that ran on degraded (fallback) data is reconstructible
            # after the fact even when nothing was bought.
            await _log_cycle_evaluation(
                contract=contract, symbol=label, timeframe_configured_min=tf_min,
                provenance=provenance, rsi_last=rsi_last, mfi_last=mfi_last,
                action=action, reason=reason,
            )

        try:
            pair = await paper_trader._default_pair_lookup(contract, chain=chain)
        except Exception as exc:  # noqa: BLE001 -- one token's failure never blocks the rest
            logger.info("scalping_v9[%s]: pair lookup failed (%s)", label, exc)
            await _log("hold", "pair_lookup_failed")
            continue
        if pair is None or not pair.price_usd or pair.price_usd <= 0:
            actions["holds"].append({"symbol": label, "reason": "no_liquid_pair"})
            await _log("hold", "no_liquid_pair")
            continue
        spot = pair.price_usd

        actions["closed"].extend(
            await _manage_positions(
                contract, spot, notifier=notifier, trail_pct=token["trail_pct"],
            )
        )

        try:
            # 06/08 -- operator-confirmed fix: this used to call GeckoTerminal
            # DIRECTLY, unlike v8 which goes through _fetch_candles's 6-stage
            # cascade (GeckoTerminal -> Mobula -> DexPaprika -> ...). A real
            # missed entry (VELVET, 06/08 17:15 UTC transition confirmed after
            # the fact from raw candles) traced back to exactly this: a
            # GeckoTerminal rate-limit (documented as recurring in this
            # codebase, especially right after a redeploy) left v9 blind with
            # no fallback for that cycle. Same mode string as before
            # ("scalping_{N}m") -- momentum_entry now recognizes it by prefix
            # (see its own comment) and routes to the scalping-only fallback
            # tier (never the day/hour-scale standard one). Known limit:
            # Mobula/DexPaprika only return fixed 15m/30m candles, not this
            # token's exact configured timeframe -- accepted as strictly
            # better than "no data at all", never day/hour-scale.
            candles = await momentum_entry._fetch_candles(
                pair.pair_address, chain, contract=contract, pair=pair,
                mode=f"scalping_{tf_min}m",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("scalping_v9[%s]: OHLCV failed (%s)", label, exc)
            await _log("hold", "ohlcv_exception")
            continue
        provenance = momentum_entry.get_last_candle_provenance()
        if not candles:
            actions["holds"].append({"symbol": label, "reason": "ohlcv_unavailable"})
            await _log("hold", "ohlcv_unavailable", provenance=provenance)
            continue
        # Last candle is the one still forming (standard real-time OHLCV
        # behavior) -- never compute the signal on an unclosed candle, same
        # centralized-trim doctrine as scalping_variants.
        candles = candles[:-1]
        if len(candles) < _MIN_CANDLES_FOR_SIGNAL:
            actions["holds"].append({"symbol": label, "reason": "insufficient_candles"})
            await _log("hold", "insufficient_candles", provenance=provenance)
            continue

        # 07/08 -- configurable per-token condition spec. A stored spec that
        # fails to parse is a HOLD, never a fallback to defaults: silently
        # trading on criteria the operator did not configure is the worse
        # failure mode (fail-closed, same doctrine as every guardrail here).
        conditions, spec_error = signal_conditions.parse(token["signals"])
        if spec_error:
            actions["holds"].append({"symbol": label, "reason": "invalid_signal_spec"})
            logger.warning(
                "scalping_v9[%s]: spec de signal invalide (%s) -- aucun achat",
                label, spec_error,
            )
            await _log("hold", f"invalid_signal_spec:{spec_error}", provenance=provenance)
            continue
        verdicts = signal_conditions.evaluate(conditions, candles)
        last_values = signal_conditions.current_values(conditions, candles)
        # The cycle-evaluation log keeps its two dedicated rsi/mfi columns --
        # they stay populated when the spec uses those indicators, and read
        # None for a spec built on others (never a value from a different
        # indicator squeezed into a column named after RSI).
        rsi_last = last_values.get("rsi")
        mfi_last = last_values.get("mfi")
        transition_idx = _find_transition_in_verdicts(verdicts)
        if transition_idx is None:
            actions["holds"].append({"symbol": label, "reason": "no_signal"})
            await _log("hold", "no_signal", rsi_last=rsi_last, mfi_last=mfi_last, provenance=provenance)
            continue

        transition_ts = float(getattr(candles[transition_idx], "ts", 0) or 0)
        if _last_buy_episode_ts.get(contract.lower()) == transition_ts:
            actions["holds"].append({"symbol": label, "reason": "episode_already_bought"})
            await _log("hold", "episode_already_bought", rsi_last=rsi_last, mfi_last=mfi_last, provenance=provenance)
            continue
        if await _recent_position_guard(contract):
            actions["holds"].append({"symbol": label, "reason": "episode_guard_recent_position"})
            await _log("hold", "episode_guard_recent_position", rsi_last=rsi_last, mfi_last=mfi_last, provenance=provenance)
            continue

        # The one hard guardrail (CLAUDE.md absolute) -- fail-closed.
        clear, hp_reason, _hp_code = await momentum_entry._check_honeypot(
            contract, chain, liquidity_usd=pair.liquidity_usd,
        )
        if not clear:
            actions["holds"].append({"symbol": label, "reason": f"honeypot:{hp_reason}"})
            logger.info("scalping_v9[%s]: honeypot gate refused (%s)", label, hp_reason)
            await _log("hold", f"honeypot:{hp_reason}", rsi_last=rsi_last, mfi_last=mfi_last, provenance=provenance)
            continue

        # 13/08 -- real gap found live: v9 checked the honeypot guardrail but
        # never the insider-concentration one every other buy path shares
        # (momentum papier, agent-wallet pilot, limit orders). Watchlist
        # tokens are operator-picked (lower a priori risk than an
        # auto-discovered candidate), but that's a mitigation, not a
        # substitute for the actual check -- reuses the same shared,
        # 7-day-cached verdict (``holder_concentration_cache``), so a token
        # already cleared elsewhere costs zero extra network call here.
        too_concentrated, concentration_reason = await momentum_entry._check_holder_concentration(
            contract, chain, pair.pair_address,
        )
        if too_concentrated:
            unverifiable = concentration_reason == momentum_entry._HOLDER_DATA_UNAVAILABLE_REASON
            reason_code = "holder_concentration_unverifiable" if unverifiable else "holder_concentration"
            actions["holds"].append({"symbol": label, "reason": f"{reason_code}:{concentration_reason}"})
            logger.info("scalping_v9[%s]: holder-concentration gate refused (%s)", label, concentration_reason)
            await _log(
                "hold", f"{reason_code}:{concentration_reason}",
                rsi_last=rsi_last, mfi_last=mfi_last, provenance=provenance,
            )
            continue

        cash = await paper_trader.cash_available(wallet=paper_trader.V9_WALLET)
        alloc = cash * BUY_PCT_OF_REMAINING_CASH
        # 07/08 -- operator request, v8 parity: never let a buy represent
        # more than risk_guard.MAX_ALLOC_PCT_OF_POOL_LIQUIDITY (1%) of the
        # pool's real liquidity. Applied HERE, on our own alloc, rather than
        # by passing pool_liquidity_usd to open_position below: that would
        # ALSO feed risk_guard.simulated_fill_price, which recomputes the
        # fill price from its OWN price-impact model on top of the fee/impact
        # this module already bakes into _degraded_buy_price -- a real double
        # count of the impact, found while wiring this (open_position's own
        # comment: "fail-open to entry_price without a known
        # pool_liquidity_usd" -- true, but the sizing cap and the fill-price
        # recompute share that one argument, so there is no way to opt into
        # one without the other from the caller side).
        alloc = risk_guard.cap_alloc_to_pool_share(alloc, pair.liquidity_usd)
        if alloc < _MIN_ALLOC_USD:
            actions["holds"].append({"symbol": label, "reason": "insufficient_cash"})
            await _log("hold", "insufficient_cash", rsi_last=rsi_last, mfi_last=mfi_last, provenance=provenance)
            continue

        # Values ON THE TRANSITION CANDLE (not the last one) -- what actually
        # triggered, for the thesis and the cycle log.
        #
        # Yes, this recomputes series `evaluate` and `current_values` already
        # built -- three passes per indicator per token per cycle. Flagged by
        # the 07/08 architectural review as a cost risk, so it was MEASURED
        # rather than assumed: worst realistic case (3 conditions including
        # the sliding-window `divergence`) is 2.1 ms per token, 19 ms for the
        # whole 9-token watchlist -- 0.006% of a 5-minute cycle. Caching would
        # save 13 ms and add shared mutable state to the buy-decision path.
        # Deliberately not done; revisit only if the watchlist grows by an
        # order of magnitude AND a profile shows this actually mattering.
        triggered_values = {
            c.indicator: signal_conditions.INDICATORS[c.indicator].series(
                candles, c.period,
            )[transition_idx]
            for c in conditions
        }
        rsi_shown = triggered_values.get("rsi")
        mfi_shown = triggered_values.get("mfi")
        triggered_text = " ET ".join(
            f"{c.indicator.upper()}({c.period})="
            f"{triggered_values[c.indicator]:.1f} {c.operator} {c.threshold:g}"
            if triggered_values.get(c.indicator) is not None
            else f"{c.indicator.upper()}({c.period}) {c.operator} {c.threshold:g}"
            for c in conditions
        )
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
                f"[V9] Signal synchronisé sur bougie {token['timeframe_min']}min : "
                f"{triggered_text} en même temps. "
                f"Achat immédiat 3% du cash restant, sortie unique : "
                f"stop suiveur -{token['trail_pct'] * 100:g}% du plus haut spot."
                + (
                    f" [Données : {provenance['provider']}, {provenance['timeframe_served']}"
                    f"{' -- DÉGRADÉ' if provenance.get('degraded') else ''}]"
                    if provenance else ""
                )
            ),
        )
        if pos is None:
            actions["holds"].append({"symbol": label, "reason": "open_refused"})
            await _log("hold", "open_refused", rsi_last=rsi_shown, mfi_last=mfi_shown, provenance=provenance)
            continue
        # Trailing runs on SPOT ("prix net réel sur le graphique") -- reseed
        # the high-water at spot, not the degraded fill open_position stored.
        await paper_trader._update_high_water(pos["id"], spot)
        _last_buy_episode_ts[contract.lower()] = transition_ts
        actions["opened"].append(pos)
        await _log("buy", "synchronized_transition", rsi_last=rsi_shown, mfi_last=mfi_shown, provenance=provenance)
        if notifier is not None:
            try:
                await notifier(paper_trader.format_buy_alert(pos))
            except Exception as exc:  # noqa: BLE001 -- alert must never block the cycle
                logger.info("scalping_v9: buy alert failed (%s)", exc)

    return actions
