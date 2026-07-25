"""Pair the flat, append-only swap journal (``agent_wallet_tx_log``) into
CLOSED / OPEN *positions* with a realized P&L — the real feed the swing-pocket
loss circuit breaker (``agent_wallet_smart_swing.evaluate_swing_risk`` /
``run_swing_post_mortem``) and the batched loss review
(``skills.trade_loss_batch_review``) were built to consume but never had one
for (Smart Account migration #41, 07/25).

Context and the exact gap this closes
-------------------------------------
``agent_wallet_log`` records ONE row per swap *attempt* (``status`` in
{"ok", "failed", "blocked"}). It has no notion of a "position": a buy and its
later sell are two independent rows. ``evaluate_swing_risk`` therefore takes
``equity_usd`` / ``recent_pnls`` as INJECTED seams, and ``run_swing_post_mortem``
takes ``recent_buys`` as an injected seam, with a docstring that literally says
that real feed "does NOT exist yet for this wallet ... see the module's HANDOFF
for this documented gap". This module IS that feed — pure read of the journal,
never a swap, never a CDP call, never a key.

Pairing model (documented assumption — read this before trusting a P&L)
----------------------------------------------------------------------
A **buy** leg is an ``ok`` swap ``USDC -> X`` (``token_in`` is USDC,
``token_out`` is the traded token X); a **sell** leg is an ``ok`` swap
``X -> USDC``. A buy followed later by a sell of the SAME token X is one closed
position with ``pnl_usd = proceeds_usd(sell) - cost_usd(buy)`` — both amounts are
already USD-denominated in the journal (a buy's ``amount_in`` is the USD spent,
a sell's ``amount_out`` is the USDC received), so the realized P&L is exact and
never depends on the token quantity.

Legs are matched **FIFO, one buy leg to one sell leg**. The swing execution
path (``execute_smart_swing_swap``) does full-position round-trips (buy the whole
lot, later sell the whole lot back to USDC), so in the intended usage there is
only ever **one open lot per token at a time** — exactly the task's stated
assumption. The FIFO queue below is a safe generalization of that: if a second
buy of the same token ever arrives before the first is sold (accumulation — a
behavior the current, dormant swing path never produces), the oldest lot is
closed first and the newer lot stays genuinely OPEN. It never invents a close.

Honest limitations (surfaced, never silent — see the module HANDOFF entry)
--------------------------------------------------------------------------
1. **A sell with no matching open buy is NEVER turned into a P&L.** It would
   need a phantom entry at price 0 (a fabricated, enormous "profit"). Such sells
   are collected in ``PairingResult.unmatched_sells`` and warned about, never
   mixed into ``closed``.
2. **Partial / accumulated fills are not quantity-reconciled.** One sell closes
   exactly one buy lot regardless of the token quantities. If future
   orchestration adds partial sells or averages into a position, the per-lot USD
   P&L becomes approximate and this pairing must be revisited (quantity-aware
   matching). It is exact for the current full-round-trip design.
3. **The journal carries no trade CONTEXT.** ``thesis`` /
   ``discovery_channel`` / ``conviction_tier`` / ``entry_regime`` /
   ``close_reason`` / ``close_notes`` do not exist in ``agent_wallet_tx_log`` —
   they are paper-portfolio concepts. They are returned as ``None`` here (the
   consumers render them "inconnu"/"(absente)"), so a batch/adversarial review
   of real swing trades sees prices and P&L only until the log (or a sibling
   table) also records the entry rationale.
4. **True mark-to-market equity is NOT computed here.** ``equity_usd`` for the
   drawdown branch of ``evaluate_swing_risk`` needs the live value of still-held
   tokens (a network price call this pure-read module must never make). This
   module supplies the fully-real ``recent_pnls`` (the consecutive-loss branch)
   and ``recent_buys`` (the post-mortem); the caller injects ``equity_usd`` from
   a live balance function. See ``evaluate_swing_risk_from_log``.

Nothing here executes a swap or touches CDP: it is a pure read of
``agent_wallet_tx_log`` and nothing else.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field

import aiosqlite

from aria_core import agent_wallet_log
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

logger = logging.getLogger(__name__)

# The swing wallet_product this module is primarily built for. Kept as a plain
# literal (mirrors ``agent_wallet_smart_swing.WALLET_PRODUCT``) rather than
# imported, to keep this pure-read module out of smart_swing's CDP/risk_guard
# import chain. A coherence test asserts the two never drift apart.
SWING_WALLET_PRODUCT = "cdp_smart_account_swing"

# A token counts as USDC (the pocket's only funding asset) if it matches the
# canonical Base USDC address, or the bare "USDC" symbol some historical/manual
# rows use. Case-insensitive: record_transaction never normalizes case.
_USDC_ALIASES = frozenset({USDC_BASE_ADDRESS.lower(), "usdc"})


def _is_usdc(token: str | None) -> bool:
    return (token or "").strip().lower() in _USDC_ALIASES


def _as_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass
class PairingResult:
    """Outcome of pairing one wallet_product's ``ok`` swap rows.

    ``closed`` / ``open`` are position dicts (most-recent-first). ``closed``
    carries a real ``pnl_usd``; ``open`` never does (its exit hasn't happened).
    ``unmatched_sells`` and ``anomalies`` exist so a data-quality problem is
    visible, never silently dropped.
    """

    closed: list[dict] = field(default_factory=list)
    open: list[dict] = field(default_factory=list)
    unmatched_sells: list[dict] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)


def _position_id(row_id: int) -> int:
    """Position id = the NEGATED source-row id (the closing swap row for a
    closed position, the opening swap row for an open one).

    Negated on purpose: paper positions use positive ``AUTOINCREMENT`` ids, and
    ``trade_loss_batch``/``trade_devils_advocate`` persist by ``position_id``.
    A negative id occupies a provably disjoint id-space from every positive
    paper id, so a real-capital swing position can never collide with a paper
    position in those SHARED tables. Buy-row ids and sell-row ids are disjoint
    subsets of the journal's row ids, so an open (-buy_id) and a closed
    (-sell_id) position can never collide with each other either. The real,
    positive row ids are preserved in ``buy_row_id`` / ``sell_row_id``.
    """
    return -abs(_as_int(row_id))


def _build_closed(buy: dict, sell: dict) -> dict:
    cost_usd = _as_float(buy.get("amount_in"))       # USD spent on the buy
    qty_bought = _as_float(buy.get("amount_out"))    # tokens received
    proceeds_usd = _as_float(sell.get("amount_out"))  # USDC received on the sell
    qty_sold = _as_float(sell.get("amount_in"))      # tokens sold
    pnl_usd = proceeds_usd - cost_usd
    pnl_pct = (pnl_usd / cost_usd * 100.0) if cost_usd > 0 else None
    entry_price = (cost_usd / qty_bought) if qty_bought > 0 else None
    exit_price = (proceeds_usd / qty_sold) if qty_sold > 0 else None
    sell_row_id = _as_int(sell.get("id"))
    return {
        "id": _position_id(sell_row_id),
        "contract": buy.get("token_out") or "",
        "symbol": None,          # the journal stores addresses, not symbols
        "chain": buy.get("chain") or "",
        "cost_usd": cost_usd,
        "proceeds_usd": proceeds_usd,
        "qty": qty_bought,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "opened_at": buy.get("created_at"),
        "closed_at": sell.get("created_at"),
        "status": "closed",
        # Context the journal genuinely does not carry -- never invented.
        "strategy": None,
        "thesis": None,
        "discovery_channel": None,
        "conviction_tier": None,
        "entry_regime": None,
        "close_reason": None,
        "close_notes": None,
        # Traceability back to the real (positive) journal rows.
        "buy_tx_hash": buy.get("tx_hash") or "",
        "sell_tx_hash": sell.get("tx_hash") or "",
        "buy_row_id": _as_int(buy.get("id")),
        "sell_row_id": sell_row_id,
    }


def _build_open(buy: dict) -> dict:
    cost_usd = _as_float(buy.get("amount_in"))
    qty_bought = _as_float(buy.get("amount_out"))
    entry_price = (cost_usd / qty_bought) if qty_bought > 0 else None
    buy_row_id = _as_int(buy.get("id"))
    return {
        "id": _position_id(buy_row_id),
        "contract": buy.get("token_out") or "",
        "symbol": None,
        "chain": buy.get("chain") or "",
        "cost_usd": cost_usd,
        "qty": qty_bought,
        "entry_price": entry_price,
        # No exit yet -- a P&L here would be an invented close (never done).
        "exit_price": None,
        "pnl_usd": None,
        "pnl_pct": None,
        "opened_at": buy.get("created_at"),
        "closed_at": None,
        "status": "open",
        "strategy": None,
        "thesis": None,
        "buy_tx_hash": buy.get("tx_hash") or "",
        "buy_row_id": buy_row_id,
    }


def _build_unmatched_sell(sell: dict) -> dict:
    """A sell with no open buy to pair against. Deliberately carries NO
    ``pnl_usd`` -- computing one would require a phantom entry (a fabricated,
    enormous profit). Surfaced for visibility, never a position."""
    return {
        "token": sell.get("token_in") or "",
        "chain": sell.get("chain") or "",
        "proceeds_usd": _as_float(sell.get("amount_out")),
        "at": sell.get("created_at"),
        "sell_tx_hash": sell.get("tx_hash") or "",
        "sell_row_id": _as_int(sell.get("id")),
    }


def pair_swaps(rows: list[dict]) -> PairingResult:
    """Pure pairing of journal rows into positions — no DB, no I/O, fully
    unit-testable. Filters to ``ok`` ``swap`` rows itself and orders them
    chronologically (``created_at`` then ``id`` as tie-break), so a caller may
    pass rows in any order and including failed/blocked/transfer rows.

    Only ``status == "ok"`` and ``action_type == "swap"`` rows are ever
    considered — a failed/blocked attempt moved no funds, and a ``transfer``
    (named exception #4) is not a trading leg.
    """
    ok_swaps = [
        r for r in rows
        if r.get("status") == "ok" and r.get("action_type") == "swap"
    ]
    # ISO-8601 UTC ``created_at`` sorts lexicographically in chronological
    # order; ``id`` breaks ties by real insertion order.
    ok_swaps.sort(key=lambda r: (str(r.get("created_at") or ""), _as_int(r.get("id"))))

    open_lots: dict[str, deque] = defaultdict(deque)  # token(lower) -> buy rows
    result = PairingResult()

    for row in ok_swaps:
        token_in = row.get("token_in") or ""
        token_out = row.get("token_out") or ""
        in_is_usdc = _is_usdc(token_in)
        out_is_usdc = _is_usdc(token_out)

        if in_is_usdc and not out_is_usdc and token_out.strip():
            # BUY: USDC -> X. Opens (or stacks) a lot for token X.
            open_lots[token_out.strip().lower()].append(row)
        elif out_is_usdc and not in_is_usdc and token_in.strip():
            # SELL: X -> USDC. Closes the OLDEST open lot for X (FIFO).
            key = token_in.strip().lower()
            lots = open_lots.get(key)
            if lots:
                buy = lots.popleft()
                result.closed.append(_build_closed(buy, row))
            else:
                result.unmatched_sells.append(_build_unmatched_sell(row))
        else:
            # Neither a clean buy nor a clean sell: USDC->USDC, X->Y (the swing
            # path never does token-to-token), or an empty leg. Never guessed.
            result.anomalies.append(row)

    for lots in open_lots.values():
        for buy in lots:
            result.open.append(_build_open(buy))

    # Most-recent-first, matching paper_trader.get_closed_positions/get_open_positions
    # (closed_at DESC, then row id DESC as the tie-break for same-timestamp closes).
    result.closed.sort(key=lambda p: (p.get("closed_at") or "", p.get("sell_row_id") or 0), reverse=True)
    result.open.sort(key=lambda p: (p.get("opened_at") or "", p.get("buy_row_id") or 0), reverse=True)

    if result.unmatched_sells or result.anomalies:
        logger.warning(
            "agent_wallet_positions: pairing surfaced %d unmatched sell(s) and %d anomaly row(s) "
            "-- never turned into a P&L (see PairingResult.unmatched_sells/anomalies)",
            len(result.unmatched_sells), len(result.anomalies),
        )
    return result


async def _fetch_ok_swap_rows(wallet_product: str) -> list[dict]:
    """All ``ok`` ``swap`` rows for one wallet_product, chronological. Reads the
    SAME database ``agent_wallet_log`` writes to (``agent_wallet_log.DB_PATH``,
    read at call time so a test monkeypatching that path is honored)."""
    await agent_wallet_log._ensure_table()
    columns = ", ".join(agent_wallet_log._COLUMNS)
    async with aiosqlite.connect(agent_wallet_log.DB_PATH) as db:
        rows = await (
            await db.execute(
                f"SELECT {columns} FROM agent_wallet_tx_log "
                "WHERE wallet_product = ? AND status = 'ok' AND action_type = 'swap' "
                "ORDER BY created_at ASC, id ASC",
                (wallet_product,),
            )
        ).fetchall()
    return [dict(zip(agent_wallet_log._COLUMNS, row)) for row in rows]


async def load_positions(wallet_product: str = SWING_WALLET_PRODUCT) -> PairingResult:
    """Read the journal for ``wallet_product`` and pair it into positions."""
    return pair_swaps(await _fetch_ok_swap_rows(wallet_product))


async def closed_positions_fetch(
    wallet_product: str = SWING_WALLET_PRODUCT, *, limit: int | None = None,
) -> list[dict]:
    """Closed positions (both legs found), most-recent-first — same shape/order
    contract as ``paper_trader.get_closed_positions``. This is the real
    ``positions_fetch`` for ``trade_loss_batch_review`` and the real
    ``recent_buys`` source for ``run_swing_post_mortem``."""
    closed = (await load_positions(wallet_product)).closed
    return closed[:limit] if limit is not None else closed


async def open_positions_fetch(wallet_product: str = SWING_WALLET_PRODUCT) -> list[dict]:
    """Still-open positions (a buy with no matching sell yet), most-recent-first.
    Never carries a P&L — an open position's exit hasn't happened."""
    return (await load_positions(wallet_product)).open


async def recent_realized_pnls(
    wallet_product: str = SWING_WALLET_PRODUCT, *, limit: int | None = None,
) -> list[float]:
    """Realized ``pnl_usd`` of closed positions, most-recent-first — exactly the
    ``recent_pnls`` shape ``evaluate_swing_risk`` consumes (its
    ``_count_consecutive_losses`` walks newest-first and stops at the first
    non-loss)."""
    closed = await closed_positions_fetch(wallet_product, limit=limit)
    return [float(p["pnl_usd"]) for p in closed if p.get("pnl_usd") is not None]


async def pairing_health(wallet_product: str = SWING_WALLET_PRODUCT) -> dict:
    """Cheap data-quality snapshot: counts of closed/open/unmatched/anomaly rows
    for this wallet_product. Lets an operator or a caller see a pairing problem
    (e.g. unmatched sells) instead of it being invisible."""
    result = await load_positions(wallet_product)
    return {
        "wallet_product": wallet_product,
        "closed": len(result.closed),
        "open": len(result.open),
        "unmatched_sells": len(result.unmatched_sells),
        "anomalies": len(result.anomalies),
    }


# ── Zero-arg swing feed + wired entry points (never a default change) ─────────
#
# The functions below wire the REAL feed above into the two consumers that were
# built with an injected seam. They are ALWAYS explicit about the swing feed --
# they never touch either consumer's default behavior (trade_loss_batch_review's
# default stays paper_trader.get_closed_positions; smart_swing keeps injecting).


async def swing_closed_positions_fetch() -> list[dict]:
    """Zero-arg closed-position fetch bound to the swing wallet_product --
    matches the ``positions_fetch`` seam signature (``async () -> list[dict]``)
    that ``trade_loss_batch_review`` / ``trade_devils_advocate`` expect."""
    return await closed_positions_fetch(SWING_WALLET_PRODUCT)


async def run_swing_loss_batch_review_cycle(*, llm=None) -> dict:
    """Run the batched loss review over the REAL swing ledger instead of the
    paper portfolio — the swing feed passed EXPLICITLY as ``positions_fetch``,
    never a change to ``trade_loss_batch_review``'s paper default.

    ⚠️ Shared-table caveat (documented, not silently introduced): ``trade_loss_
    batch_review`` persists into the ``trade_loss_batch`` tables, and
    ``momentum_entry`` injects their ``active_trajectory_adjustments()``
    GLOBALLY into the paper momentum prompt. Position-id collisions are already
    prevented (swing ids are negative, see ``_position_id``), but a CONFIRMED
    swing adjustment would still surface in the paper momentum prompt because
    that active-set read is not source-scoped. This entry point is therefore
    deliberately NOT wired to any heartbeat here: wiring it to production needs
    that leak resolved first (a source/pocket column filtered by
    ``active_trajectory_adjustments``, or a separate table for the real-capital
    feed). See the module HANDOFF entry."""
    from aria_core.skills import trade_loss_batch_review as tlbr

    return await tlbr.run_trade_loss_batch_review_cycle(
        llm=llm, positions_fetch=swing_closed_positions_fetch,
    )


async def evaluate_swing_risk_from_log(
    *,
    equity_usd: float,
    wallet_product: str = SWING_WALLET_PRODUCT,
    notify_fn=None,
    post_mortem_llm=None,
    recent_buys_limit: int = 5,
):
    """Drive ``agent_wallet_smart_swing.evaluate_swing_risk`` with the REAL feed
    from the journal, replacing its injected ``recent_pnls`` / ``recent_buys``
    seams:

      - ``recent_pnls`` <- ``recent_realized_pnls`` (real, drives the
        consecutive-loss branch),
      - ``post_mortem_fn`` <- ``run_swing_post_mortem(recent closed buys)``
        (real adversarial review on a fresh trip),

    while ``equity_usd`` stays INJECTED by the caller: true mark-to-market
    equity of still-held tokens needs a live price call this pure-read module
    must never make (limitation #4 in the module docstring). The caller passes
    a live balance as ``equity_usd`` (e.g. the same balance function the swing
    execution path uses)."""
    from aria_core import agent_wallet_smart_swing as sws

    pnls = await recent_realized_pnls(wallet_product)
    recent_buys = await closed_positions_fetch(wallet_product, limit=recent_buys_limit)

    async def post_mortem_fn() -> str:
        return await sws.run_swing_post_mortem(recent_buys, llm=post_mortem_llm)

    return await sws.evaluate_swing_risk(
        equity_usd=equity_usd,
        recent_pnls=pnls,
        post_mortem_fn=post_mortem_fn,
        notify_fn=notify_fn,
    )
