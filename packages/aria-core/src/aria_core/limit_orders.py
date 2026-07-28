"""Limit-order mechanism for the momentum paper-trading pipeline (07/23,
operator-designed and cross-reviewed before implementation).

The problem it solves: between signal detection and execution, a candidate
goes through honeypot/OHLCV/LLM analysis -- on a volatile token, the price can
drift upward enough that the R/R at execution no longer clears the entry bar
(``paper_trader._execution_rr_still_valid``). Until now this was a plain
reject (``funnel["price_stale_at_execution"]``), discarding a setup that only
got MORE EXPENSIVE, not a dead one -- the real CHECK case (0.038 signal price
-> 0.044 execution price, R/R degraded from 3.9 to 1.52).

Instead of rejecting outright, a limit order is placed at the ORIGINAL signal
price and watched by ``momentum_websocket._drain_once()`` (already polling
prices every 30s) until the price comes back down to it, the structure
breaks (invalidation crossed), or it expires (``LIMIT_ORDER_EXPIRY_HOURS``).

Two cases are drawn explicitly, never conflated:
  (a) structure already broken (fresh price through the invalidation, or a
      security re-check fails) -> reject outright, exactly as before this
      mechanism existed. A limit order is NEVER placed on a dead setup.
  (b) the setup only drifted upward, structure still intact -> a limit order
      is worth placing, waiting for a pullback to the original price.

State machine: ``pending`` (just placed, price still far above target) ->
``watching`` (price within ``LIMIT_ORDER_WATCH_TRIGGER_MULT`` of target, one
re-analysis performed at this transition) -> ``triggered`` (bought) /
``cancelled`` (invalidation crossed, or the re-analysis failed) / ``expired``
(silent, just logged -- never a Telegram alert for a setup that simply never
came back).

27/07 -- 3-pocket architecture plan, Phase 2 (see paper_trader.py's own
``multi_pocket_sourcing_enabled()``/``_open_new_entries_for_wallet``): every
pending order now remembers which pocket ("swing"/"scalping"/"vc") placed it
(``wallet`` column, additive hot migration -- default 'swing', same migration
decision as ``paper_position.wallet``) and executes into that SAME pocket,
never a hardcoded one. ``has_active_order``/``create_pending_order`` default
to ``wallet="swing"`` -- unchanged behavior for any caller that doesn't pass
it explicitly, i.e. every caller while ``paper_trader.
multi_pocket_sourcing_enabled()`` is OFF (the only caller today,
``paper_trader._open_new_entries_for_wallet``, always passes its own
``wallet=`` explicitly)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services.dexscreener import token_url

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Explicit operator decisions, 07/23 (design cross-reviewed before coding).
LIMIT_ORDER_WATCH_TRIGGER_MULT = 1.10  # enters "watching" once price <= target * 1.10
LIMIT_ORDER_EXPIRY_HOURS = 3.0  # short-lived -- momentum setups go stale fast

# Item #158, 28/07: a bonding-curve token still sitting near
# bonding_entry._MIN_LIQUIDITY_USD (5,000$, #167) moves too erratically for a
# "wait for the price to come back down" mechanism to mean anything -- the
# whole premise of a limit order (a pullback to a still-valid original setup)
# assumes some baseline stability this thin a market doesn't have yet.
# liquidity_usd is used as the market-cap proxy here, same doctrine already
# established in bonding_entry.py ("liquidité quasiment 1 pour 1 avec le
# market cap" on a bonding curve) -- never a separate $VIRTUAL->USD mcap
# conversion just for this gate. Starting value, to recalibrate once real
# bonding limit orders accumulate outcomes.
BONDING_LIMIT_ORDER_MIN_LIQUIDITY_USD = 20_000.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 27/07 -- 3-pocket architecture plan, Phase 2: additive hot-migration list,
# same idempotent idiom as paper_trader.py's own ``_ADDED_COLUMNS`` (see its
# comment for why -- SQLite doesn't add a column to an already-existing table
# just because ``CREATE TABLE IF NOT EXISTS`` changed). Default 'swing' for
# every order placed before this work, and for every order placed while
# ``paper_trader.multi_pocket_sourcing_enabled()`` is OFF.
_ADDED_COLUMNS = [
    ("wallet", "TEXT NOT NULL DEFAULT 'swing'"),
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_limit_order (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                symbol TEXT,
                target_price REAL NOT NULL,
                signal_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                watch_entered_at TEXT,
                resolved_at TEXT,
                cancel_reason TEXT
            )
            """
        )
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(pending_limit_order)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE pending_limit_order ADD COLUMN {name} {ddl}")
        await db.commit()


def should_place_limit_order(
    signal_price: float | None, fresh_price: float | None, invalidation_price: float | None,
    *, chain: str | None = None, liquidity_usd: float | None = None,
) -> bool:
    """True only for case (b): the setup drifted upward since the signal
    (``fresh_price`` above ``signal_price``) but the structure is still
    intact (``fresh_price`` still above ``invalidation_price``). False for
    case (a) -- the structure already broke (price at or below the
    invalidation) -- a dead setup is rejected outright, never turned into a
    limit order. Fail-closed (``False``) on any missing input.

    ``chain``/``liquidity_usd`` (Item #158, 28/07): for a bonding-curve
    candidate specifically (``chain == bonding_entry.CHAIN_MARKER``), an
    ADDITIONAL market-cap-proxy floor applies (``BONDING_LIMIT_ORDER_MIN_
    LIQUIDITY_USD``) -- see that constant's own comment for why. Both
    ``None`` (the default, every non-bonding caller) -- unchanged behavior."""
    from aria_core.bonding_entry import CHAIN_MARKER

    if not signal_price or not fresh_price or not invalidation_price:
        return False
    if fresh_price <= invalidation_price:
        return False  # structure already broken -- dead setup
    if chain == CHAIN_MARKER and (
        liquidity_usd is None or liquidity_usd < BONDING_LIMIT_ORDER_MIN_LIQUIDITY_USD
    ):
        return False
    return fresh_price > signal_price


def should_enter_watching(target_price: float, current_price: float | None) -> bool:
    """True once ``current_price`` has come down to within
    ``LIMIT_ORDER_WATCH_TRIGGER_MULT`` of the target -- worth a re-analysis
    (honeypot + invalidation) before committing to close, active monitoring."""
    if not current_price or current_price <= 0:
        return False
    return current_price <= target_price * LIMIT_ORDER_WATCH_TRIGGER_MULT


def check_watching_order(
    target_price: float, invalidation_price: float | None, current_price: float | None,
) -> str:
    """Decision for an order already in ``watching`` state: ``'trigger'``
    (price reached the target -- buy now), ``'cancel'`` (invalidation
    crossed during the watch -- the setup died while ARIA was waiting for a
    pullback), or ``'wait'`` (still watching). Missing price -> ``'wait'``,
    never a decision on unknown data."""
    if not current_price or current_price <= 0:
        return "wait"
    if invalidation_price and current_price <= invalidation_price:
        return "cancel"
    if current_price <= target_price:
        return "trigger"
    return "wait"


async def has_active_order(contract: str, chain: str, *, wallet: str = "swing") -> bool:
    """True if a ``pending`` or ``watching`` order already exists for this
    contract IN THIS POCKET -- never stacks a second limit order on the same
    candidate within the same pocket.

    ``wallet`` (27/07, 3-pocket architecture plan): defaults to ``"swing"`` --
    unchanged behavior for any caller that doesn't pass it (gate OFF, or any
    caller predating this work). Scoped like ``paper_trader.has_open(...,
    wallet=...)`` -- a pending order already placed by a DIFFERENT pocket on
    the same contract must never block this one (the whole point of 3
    concurrent pockets: each independently detects/watches its own setup)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM pending_limit_order WHERE contract = ? AND chain = ? "
            "AND wallet = ? AND state IN ('pending', 'watching') LIMIT 1",
            (contract, chain, wallet),
        ) as cur:
            row = await cur.fetchone()
    return row is not None


async def create_pending_order(
    contract: str, chain: str, symbol: str, target_price: float, sig: dict, *, wallet: str = "swing",
) -> dict:
    """Places a new limit order at ``target_price`` (the signal's original
    price, before it drifted) -- ``sig`` is the FULL evaluated signal,
    serialized as-is so a later trigger never needs to re-scan from scratch.
    Every field of the caller's real signal dicts is already a plain
    str/float/int/bool/None (verified against ``momentum_entry``'s BUY
    returns) -- ``default=str`` below is a defensive fallback only, never
    relied on in practice.

    ``wallet`` (27/07, 3-pocket architecture plan): which pocket this order
    belongs to -- persisted as-is, read back by ``_execute_trigger`` so the
    eventual buy books into the SAME pocket that detected the setup, never a
    hardcoded one. Defaults to ``"swing"`` -- unchanged behavior (implicit
    single pocket) for any caller that doesn't pass it, i.e. every caller
    while ``paper_trader.multi_pocket_sourcing_enabled()`` is OFF."""
    await _ensure_table()
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=LIMIT_ORDER_EXPIRY_HOURS)).isoformat()
    signal_json = json.dumps(sig, default=str)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO pending_limit_order
              (contract, chain, symbol, target_price, signal_json, state, created_at, expires_at, wallet)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (contract, chain, symbol or "", target_price, signal_json, now.isoformat(), expires_at, wallet),
        )
        await db.commit()
        order_id = cur.lastrowid
    return {
        "id": order_id, "contract": contract, "chain": chain, "symbol": symbol or "",
        "target_price": target_price, "signal_json": signal_json, "state": "pending",
        "created_at": now.isoformat(), "expires_at": expires_at,
        "watch_entered_at": None, "resolved_at": None, "cancel_reason": None,
        "wallet": wallet,
    }


async def get_active_orders() -> list[dict]:
    """Every order still ``pending`` or ``watching`` -- what
    ``momentum_websocket._drain_once()`` must check on every pass."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_limit_order WHERE state IN ('pending', 'watching') "
            "ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _set_state(order_id: int, state: str, *, cancel_reason: str | None = None) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        if state == "watching":
            await db.execute(
                "UPDATE pending_limit_order SET state = ?, watch_entered_at = ? WHERE id = ?",
                (state, _now(), order_id),
            )
        else:
            await db.execute(
                "UPDATE pending_limit_order SET state = ?, resolved_at = ?, cancel_reason = ? WHERE id = ?",
                (state, _now(), cancel_reason, order_id),
            )
        await db.commit()


async def transition_to_watching(order_id: int) -> None:
    await _set_state(order_id, "watching")


async def mark_triggered(order_id: int) -> None:
    await _set_state(order_id, "triggered")


async def mark_cancelled(order_id: int, reason: str) -> None:
    await _set_state(order_id, "cancelled", cancel_reason=reason)


async def sweep_expired() -> list[dict]:
    """Marks every ``pending``/``watching`` order past ``expires_at`` as
    ``expired`` -- silent by design (never a Telegram alert, see module
    docstring), only returned here for logging by the caller."""
    await _ensure_table()
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_limit_order WHERE state IN ('pending', 'watching') "
            "AND expires_at < ?",
            (now,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            await db.execute(
                "UPDATE pending_limit_order SET state = 'expired', resolved_at = ? "
                "WHERE id IN ({})".format(",".join("?" * len(rows))),
                (now, *[r["id"] for r in rows]),
            )
            await db.commit()
    return rows


async def _reanalyze_bonding_for_watching(order: dict) -> bool:
    """Item #158, 28/07: GoPlus (the standard honeypot re-check below) is
    structurally inapplicable to a bonding-curve token -- no separate DEX
    pool/token contract to exploit beyond the protocol's own, see
    bonding_entry.py's own docstring. This is the bonding-native equivalent:
    re-checks the SAME structural hard gates ``evaluate_bonding_entry``
    itself enforces at signal time (dev-rug guard + liquidity floor) -- never
    the composite score itself (already judged once, this is a structural
    safety re-check before committing to watch closely, same scope/intent as
    the honeypot re-check it mirrors). Fail-closed on any missing/unresolved
    data, same doctrine as the rest of this module."""
    from aria_core import bonding_entry
    from aria_core.services.virtuals import virtuals_client

    try:
        token = await virtuals_client.fetch_by_address(order["contract"], chain="BASE")
    except Exception as exc:  # noqa: BLE001 -- fail-closed, never an unguarded watch
        logger.info(
            "limit_orders: bonding re-analysis failed for %s (%s) -- cancelling", order["contract"], exc,
        )
        return False
    if token is None:
        return False
    if token.dev_holding_pct is None or token.dev_holding_pct > bonding_entry._MAX_DEV_HOLDING_PCT:
        return False
    if token.liquidity_usd is None or token.liquidity_usd < bonding_entry._MIN_LIQUIDITY_USD:
        return False
    return True


async def reanalyze_for_watching(order: dict) -> bool:
    """Single re-analysis performed ONCE, at the ``pending`` -> ``watching``
    transition (never repeated on every tick while watching -- see module
    docstring): re-checks the honeypot guard (the only hard guardrail this
    pipeline enforces) since it's been up to ``LIMIT_ORDER_EXPIRY_HOURS``
    since the original scan. ``True`` -> safe to start watching closely,
    ``False`` -> cancel immediately (a newly-appeared trap is worse than a
    missed entry).

    Item #158, 28/07: a bonding-curve order (``order["chain"] ==
    bonding_entry.CHAIN_MARKER``) is routed to ``_reanalyze_bonding_for_
    watching`` instead -- calling GoPlus with this marker as a "chain" would
    either error out or silently check the wrong thing."""
    from aria_core.bonding_entry import CHAIN_MARKER

    if order["chain"] == CHAIN_MARKER:
        return await _reanalyze_bonding_for_watching(order)

    from aria_core.momentum_entry import check_honeypot

    try:
        clear, _reason, _code = await check_honeypot(order["contract"], order["chain"])
    except Exception as exc:  # noqa: BLE001 -- fail-closed, never an unguarded watch
        logger.info(
            "limit_orders: re-analysis failed for %s (%s) -- cancelling", order["contract"], exc,
        )
        return False
    return clear


async def process_active_orders(price_lookup, notifier=None) -> dict:
    """Orchestrates every active limit order for one pass of the caller's
    drain loop (``momentum_websocket._drain_once()``): expires stale orders,
    advances ``pending`` orders toward ``watching`` (with the one-time
    re-analysis), and resolves ``watching`` orders (trigger the buy, or
    cancel on a broken structure). ``price_lookup(contract, chain=...)``
    matches the same contract already used everywhere else in this pipeline.
    Never raises -- a failure on one order never blocks the others or the
    caller's own drain."""
    actions: dict = {"expired": 0, "entered_watching": 0, "cancelled": 0, "triggered": []}

    expired = await sweep_expired()
    actions["expired"] = len(expired)

    for order in await get_active_orders():
        try:
            price = await price_lookup(order["contract"], chain=order["chain"])
        except Exception as exc:  # noqa: BLE001 -- one failed lookup never blocks the others
            logger.info("limit_orders: price lookup failed for %s (%s)", order["contract"], exc)
            continue
        if not price or price <= 0:
            continue

        sig = json.loads(order["signal_json"])

        if order["state"] == "pending":
            if not should_enter_watching(order["target_price"], price):
                continue
            if await reanalyze_for_watching(order):
                await transition_to_watching(order["id"])
                actions["entered_watching"] += 1
            else:
                await mark_cancelled(order["id"], "reanalysis_failed")
                actions["cancelled"] += 1
                if notifier:
                    try:
                        await notifier(format_limit_order_cancelled_alert(order, "reanalysis_failed"))
                    except Exception:  # noqa: BLE001
                        pass
            continue

        # order["state"] == "watching"
        decision = check_watching_order(order["target_price"], sig.get("invalidation"), price)
        if decision == "cancel":
            await mark_cancelled(order["id"], "invalidation_crossed")
            actions["cancelled"] += 1
            if notifier:
                try:
                    await notifier(format_limit_order_cancelled_alert(order, "invalidation_crossed"))
                except Exception:  # noqa: BLE001
                    pass
        elif decision == "trigger":
            pos = await _execute_trigger(order, sig, price, notifier)
            if pos:
                actions["triggered"].append(pos)
                await mark_triggered(order["id"])
            # A failed trigger (open_position refused -- cap reached, cash
            # short, etc.) leaves the order in "watching": it may still fill
            # on the next pass if conditions change, rather than being lost
            # silently on a transient portfolio-level constraint.

    return actions


def _wallet_position_cap(paper_trader_module, wallet: str) -> int | None:
    """27/07 -- 3-pocket architecture plan, Phase 2: the position-count cap a
    TRIGGERED limit order must respect, mirroring ``paper_trader.
    _run_paper_cycle_locked``'s own multi-pocket branch (its "pocket_cap"
    tuple). ``paper_trader_module`` is the already-imported module reference
    from the caller (``_execute_trigger``'s own deferred import) -- avoids a
    module-level import of ``paper_trader`` here, which would create a
    circular import (``paper_trader.py`` itself imports this module locally).

    Gate OFF: byte-for-byte unchanged legacy behavior -- the flat
    ``MAX_POSITIONS`` (30) this function has always used, regardless of
    wallet (always "swing" while the gate is off, see
    ``create_pending_order``'s default). Gate ON: the REAL per-pocket cap
    (5/15/unlimited)."""
    if not paper_trader_module.multi_pocket_sourcing_enabled():
        return paper_trader_module.MAX_POSITIONS
    return {
        "scalping": paper_trader_module.MAX_POSITIONS_SCALPING,
        "swing": paper_trader_module.MAX_POSITIONS_SWING,
        "vc": paper_trader_module.MAX_POSITIONS_VC,
    }.get(wallet, paper_trader_module.MAX_POSITIONS)


async def _execute_trigger(order: dict, sig: dict, current_price: float, notifier) -> dict | None:
    """Buys at the limit-order trigger -- same pipeline as a direct buy
    (``paper_trader.open_position``/``format_buy_alert``), sizing recomputed
    with FRESH context (regime/risk_state/weekly may have moved since the
    order was placed) via the exact same ``compute_entry_alloc`` formula.
    ``current_price`` (the real spot price, NOT pre-degraded) is handed to
    ``open_position`` as-is -- it already applies its own risk cap,
    price-impact cap, and ``simulated_fill_price`` internally (same as a
    direct buy in ``_run_paper_cycle_locked``); computing them here too would
    apply the price-impact model TWICE on an already-degraded price, silently
    collapsing the allocation to zero (real bug found while testing this
    function).

    ``order['wallet']`` (27/07, 3-pocket architecture plan): the pocket THIS
    order was placed for (see ``create_pending_order``) -- every check below
    (duplicate guard, position cap, starting capital, weekly pacing context)
    is scoped to THIS SAME pocket, and the resulting buy books into it, never
    a hardcoded "swing". Falls back to "swing" if absent (an order row from
    before this work, or created while the gate was OFF)."""
    from aria_core import bonding_entry, paper_trader, risk_guard
    from aria_core.skills import market_sentiment

    wallet = order.get("wallet") or "swing"

    if await paper_trader.has_open(order["contract"], wallet=wallet):
        return None  # already bought some other way in the meantime -- never a duplicate

    max_positions_cap = _wallet_position_cap(paper_trader, wallet)
    if (
        max_positions_cap is not None
        and len(await paper_trader.get_open_positions(wallet=wallet)) >= max_positions_cap
    ):
        return None

    # 27/07 -- 3-pocket architecture plan, Phase 3: risk_guard's circuit
    # breaker is now per-pocket -- checked against THIS order's OWN pocket
    # (``wallet``, resolved above), never a stale unscoped call that would
    # let a different pocket's drawdown wrongly block/allow this trigger.
    risk_state = await risk_guard.evaluate_portfolio_risk(wallet)
    if risk_state.blocked:
        return None  # this pocket's circuit breaker armed since the order was placed

    start = await paper_trader.starting_capital(wallet=wallet)
    weekly_context = None
    try:
        cap = start
        target = paper_trader.weekly_target_equity(cap)
        started_dt = datetime.fromisoformat(await paper_trader.cycle_started_at(wallet=wallet))
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        elapsed_days = (datetime.now(timezone.utc) - started_dt).total_seconds() / 86400.0
        progress_pct = (risk_state.equity / cap - 1.0) * 100.0 if cap else 0.0
        target_pct = (paper_trader.WEEKLY_TARGET_MULTIPLIER - 1.0) * 100.0
        weekly_context = {
            "cycle_number": await paper_trader.get_current_cycle_number(wallet=wallet),
            "day": min(paper_trader.WEEKLY_CYCLE_DAYS, int(elapsed_days) + 1),
            "days_total": paper_trader.WEEKLY_CYCLE_DAYS,
            "equity": risk_state.equity,
            "target_equity": target,
            "progress_pct": progress_pct,
            "remaining_pct": target_pct - progress_pct,
        }
    except Exception as exc:  # noqa: BLE001 -- never blocking, degrades to no pacing context
        logger.info("limit_orders: weekly context unavailable at trigger (%s)", exc)
        weekly_context = None

    entry_alloc_usd, conviction_tier = paper_trader.compute_entry_alloc(
        sig, start, weekly_context, risk_state,
    )
    # Item #158, 28/07: a bonding trigger must go through the SAME extra
    # sizing steps as a direct bonding buy in paper_trader.py's own
    # _open_new_entries_for_wallet (BONDING_SIZE_REDUCTION + the #156
    # supply-proportion cap) -- without this, a limit-order trigger on a
    # bonding candidate would silently skip both, a real gap this closes.
    if order["chain"] == bonding_entry.CHAIN_MARKER:
        entry_alloc_usd *= bonding_entry.BONDING_SIZE_REDUCTION
        entry_alloc_usd = bonding_entry.cap_alloc_to_supply_pct(
            entry_alloc_usd, current_price, sig.get("total_supply"), conviction_tier,
        )
        # Item #165, 28/07: same tighten-only long-cycle macro lever as the
        # direct-buy path (paper_trader.py) -- best-effort, degrades to no
        # change on any failure.
        try:
            from aria_core.skills import btc_cycles

            btc_phase = await btc_cycles.fetch_current_macro_phase()
            btc_phase_label = btc_phase.get("label") if btc_phase else None
        except Exception as exc:  # noqa: BLE001 -- never blocking
            logger.info("limit_orders: btc_cycles macro phase unavailable (%s)", exc)
            btc_phase_label = None
        entry_alloc_usd *= bonding_entry.late_cycle_size_multiplier(btc_phase_label)

    try:
        current_regime = await market_sentiment.resolve_meta_regime()
    except Exception:  # noqa: BLE001
        current_regime = market_sentiment.META_REGIME_NEUTRAL

    thesis_prefix = (sig.get("these") or "; ".join(sig.get("reasons") or []) or "").strip()
    thesis = (
        thesis_prefix
        + f" [ordre limite -- placé à {order['target_price']:.6g}, "
        f"déclenché à {current_price:.6g}]"
    ).strip()
    pos = await paper_trader.open_position(
        order["contract"],
        order["symbol"],
        current_price,
        # 27/07 -- 3-pocket architecture plan (Phase 2): books into the SAME
        # pocket that placed this order (see docstring above) -- "swing" under
        # gate OFF (unchanged historical behavior, every order created there
        # implicitly belongs to "swing").
        wallet=wallet,
        target_price=sig.get("target"),
        invalidation_price=sig.get("invalidation"),
        alloc_usd=entry_alloc_usd,
        category=sig.get("category", ""),
        entry_security_json=sig.get("entry_security_json", ""),
        chain=order["chain"],
        thesis=thesis,
        pool_liquidity_usd=sig.get("liquidity_usd"),
        entry_atr_pct=sig.get("entry_atr_pct"),
        strategy=sig.get("strategy") or "momentum",
        entry_regime=current_regime,
        entry_dev_sold_pct=sig.get("dev_sold_pct"),
        rr=sig.get("rr"),
        align_score=sig.get("align_score"),
        conviction_tier=conviction_tier,
        rvol_multiple=sig.get("rvol_multiple"),
        discovery_channel="limit_order",
        conviction_process_trail=sig.get("conviction_process_trail"),
        conviction_website_corroborated=sig.get("conviction_website_corroborated"),
        conviction_posting_cadence=sig.get("conviction_posting_cadence"),
        liquidity_rotation_score=sig.get("liquidity_rotation_score"),
        liquidity_rotation_accelerating=sig.get("liquidity_rotation_accelerating"),
        liquidity_rotation_volume_ratio=sig.get("liquidity_rotation_volume_ratio"),
    )
    if pos and notifier:
        try:
            await notifier(paper_trader.format_buy_alert(pos))
        except Exception:  # noqa: BLE001
            pass
    return pos


def format_limit_order_placed_alert(order: dict) -> str:
    name = order.get("symbol") or (order.get("contract") or "")[:10]
    lines = [
        "🎯 ORDRE LIMITE POSÉ (portefeuille papier, aucun argent réel)",
        f"{name} -- cible {order['target_price']:.6g}",
        f"Expire dans {LIMIT_ORDER_EXPIRY_HOURS:.0f}h si le prix ne redescend jamais à ce niveau.",
    ]
    if order.get("contract"):
        lines.append(f"DexScreener : {token_url(order['contract'], chain=order.get('chain') or 'base')}")
    return "\n".join(lines)


def format_limit_order_cancelled_alert(order: dict, reason: str) -> str:
    name = order.get("symbol") or (order.get("contract") or "")[:10]
    reason_label = {
        "invalidation_crossed": "le prix a cassé l'invalidation pendant l'attente",
        "reanalysis_failed": "re-vérification sécurité échouée (honeypot)",
    }.get(reason, reason)
    return (
        f"❌ Ordre limite annulé {name} -- {reason_label}. "
        f"Cible {order['target_price']:.6g} jamais atteinte dans de bonnes conditions."
    )
