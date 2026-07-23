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
came back)."""
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        await db.commit()


def should_place_limit_order(
    signal_price: float | None, fresh_price: float | None, invalidation_price: float | None,
) -> bool:
    """True only for case (b): the setup drifted upward since the signal
    (``fresh_price`` above ``signal_price``) but the structure is still
    intact (``fresh_price`` still above ``invalidation_price``). False for
    case (a) -- the structure already broke (price at or below the
    invalidation) -- a dead setup is rejected outright, never turned into a
    limit order. Fail-closed (``False``) on any missing input."""
    if not signal_price or not fresh_price or not invalidation_price:
        return False
    if fresh_price <= invalidation_price:
        return False  # structure already broken -- dead setup
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


async def has_active_order(contract: str, chain: str) -> bool:
    """True if a ``pending`` or ``watching`` order already exists for this
    contract -- never stacks a second limit order on the same candidate."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM pending_limit_order WHERE contract = ? AND chain = ? "
            "AND state IN ('pending', 'watching') LIMIT 1",
            (contract, chain),
        ) as cur:
            row = await cur.fetchone()
    return row is not None


async def create_pending_order(
    contract: str, chain: str, symbol: str, target_price: float, sig: dict,
) -> dict:
    """Places a new limit order at ``target_price`` (the signal's original
    price, before it drifted) -- ``sig`` is the FULL evaluated signal,
    serialized as-is so a later trigger never needs to re-scan from scratch.
    Every field of the caller's real signal dicts is already a plain
    str/float/int/bool/None (verified against ``momentum_entry``'s BUY
    returns) -- ``default=str`` below is a defensive fallback only, never
    relied on in practice."""
    await _ensure_table()
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=LIMIT_ORDER_EXPIRY_HOURS)).isoformat()
    signal_json = json.dumps(sig, default=str)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO pending_limit_order
              (contract, chain, symbol, target_price, signal_json, state, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (contract, chain, symbol or "", target_price, signal_json, now.isoformat(), expires_at),
        )
        await db.commit()
        order_id = cur.lastrowid
    return {
        "id": order_id, "contract": contract, "chain": chain, "symbol": symbol or "",
        "target_price": target_price, "signal_json": signal_json, "state": "pending",
        "created_at": now.isoformat(), "expires_at": expires_at,
        "watch_entered_at": None, "resolved_at": None, "cancel_reason": None,
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


async def reanalyze_for_watching(order: dict) -> bool:
    """Single re-analysis performed ONCE, at the ``pending`` -> ``watching``
    transition (never repeated on every tick while watching -- see module
    docstring): re-checks the honeypot guard (the only hard guardrail this
    pipeline enforces) since it's been up to ``LIMIT_ORDER_EXPIRY_HOURS``
    since the original scan. ``True`` -> safe to start watching closely,
    ``False`` -> cancel immediately (a newly-appeared trap is worse than a
    missed entry)."""
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
    function)."""
    from aria_core import paper_trader, risk_guard
    from aria_core.skills import market_sentiment

    if await paper_trader.has_open(order["contract"]):
        return None  # already bought some other way in the meantime -- never a duplicate

    if len(await paper_trader.get_open_positions()) >= paper_trader.MAX_POSITIONS:
        return None

    risk_state = await risk_guard.evaluate_portfolio_risk()
    if risk_state.blocked:
        return None  # portfolio-level circuit breaker armed since the order was placed

    start = await paper_trader.starting_capital()
    weekly_context = None
    try:
        cap = start
        target = paper_trader.weekly_target_equity(cap)
        started_dt = datetime.fromisoformat(await paper_trader.cycle_started_at())
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        elapsed_days = (datetime.now(timezone.utc) - started_dt).total_seconds() / 86400.0
        progress_pct = (risk_state.equity / cap - 1.0) * 100.0 if cap else 0.0
        target_pct = (paper_trader.WEEKLY_TARGET_MULTIPLIER - 1.0) * 100.0
        weekly_context = {
            "cycle_number": await paper_trader.get_current_cycle_number(),
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
