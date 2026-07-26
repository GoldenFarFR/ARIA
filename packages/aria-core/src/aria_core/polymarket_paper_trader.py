"""Polymarket paper portfolio (Item #108, 26/07) -- ARIA "bets" on real
prediction markets with FICTITIOUS capital, structurally separate from the
$1M momentum/VC-thesis portfolio (`paper_trader.py`).

Explicit operator decisions this chantier was built under:
- Paper only, no real order, no wallet, no KYC -- capital real would require
  a separate legal/custody diligence, never assumed here.
- "aria doit miser la ou c'est recherche lui permettent d'avoir un taux de
  reussite importante" / "je suis sur a 85%" / "systeme de probabilite de
  qualite" -- see `skills/polymarket_thesis.py` for the judgment engine that
  enforces this (MIN_WIN_PROBABILITY, multi-vote convergence). This module
  only sizes and books what that engine already decided to bet on.

Structurally different from `paper_trader.py`: a prediction-market position
resolves BINARILY at a fixed date (payout is exactly $1 or $0 per share,
never a gradual price move) -- there is no trailing stop, no take-profit
tier, no ATR. Position management here is just "check if resolved yet."
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services.polymarket import PolymarketCandidateMarket, polymarket_client
from aria_core.skills.polymarket_thesis import PolymarketJudgment, estimate_market_probability

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Dedicated pocket, separate from the $1M momentum test (STARTING_CAPITAL_USD
# in paper_trader.py) -- this is its own independent diagnostic run, not a
# slice of that portfolio.
STARTING_CAPITAL_USD = 100_000.0

# Fractional Kelly (never full Kelly -- full Kelly is famously high-variance
# even with a genuinely correct probability, and ARIA's probability is an
# ESTIMATE, not a certainty). 0.25 ("quarter Kelly") is a standard
# risk-reduction factor in prediction-market/sports-betting sizing practice.
KELLY_FRACTION = 0.25

# Hard ceiling regardless of what Kelly suggests -- same "never all-in on a
# formula alone" doctrine as the momentum pipeline's own hard caps
# (risk_guard.py). Operator-adjustable once real resolved bets accumulate.
MAX_BET_PCT = 0.05

# Concurrency ceiling -- real cash availability is the natural brake (same as
# momentum's scalping mode), this just guards against an unbounded number of
# tiny concurrent bets diluting attention/observability.
MAX_OPEN_POSITIONS = 15

# How many NEW candidate markets get a full judgment (research + 3 LLM votes)
# per cycle -- caps the real cost of a single pass, independent of how many
# liquid markets exist.
#
# 26/07 -- calibrated against the REAL shared Tavily monthly budget (never
# guessed): checked live, `tavily_budget.monthly_status()` reports a 900
# credits/month cap shared across EVERY Tavily caller in this codebase (X/
# Website/Docs substance, conviction research, operator/visitor web_verify
# questions) -- 110 spent so far this month, 790 remaining at calibration
# time. The FIRST value tried here (8, at heartbeat's 240min cadence = 6
# cycles/day) would cost up to 8*6=48 credits/day = ~1440/month for THIS
# chantier ALONE -- more than the entire system's shared cap, before
# counting anything else. Recalibrated to 3 (paired with a 720min/12h cycle
# in heartbeat.py, 2 cycles/day) = up to 6 credits/day = ~180/month (~20% of
# the shared cap) -- a deliberately small, non-dominant share since this is
# one exploratory consumer among several already-established ones. Same
# "start conservative, measure, loosen later" doctrine as MIN_EDGE_
# PROBABILITY/MIN_WIN_PROBABILITY above.
CANDIDATES_PER_CYCLE = 3


# 26/07 -- Item #109, drawdown circuit breaker (polymarket_risk_guard.py):
# same migration pattern as paper_trader.py's _STATE_ADDED_COLUMNS (idempotent
# ALTER TABLE, never destructive) -- the high-water mark lives on THIS
# module's own state table, never shared with the $1M momentum portfolio's
# equity_high_water_mark (structurally separate pockets, separate risk state).
_STATE_ADDED_COLUMNS = [
    ("equity_high_water_mark", "REAL"),
]


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS polymarket_paper_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                starting_capital REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        state_existing = {
            row[1] for row in await (await db.execute("PRAGMA table_info(polymarket_paper_state)")).fetchall()
        }
        for name, ddl in _STATE_ADDED_COLUMNS:
            if name not in state_existing:
                await db.execute(f"ALTER TABLE polymarket_paper_state ADD COLUMN {name} {ddl}")
        await db.execute(
            "INSERT OR IGNORE INTO polymarket_paper_state (id, starting_capital, created_at) VALUES (1, ?, ?)",
            (STARTING_CAPITAL_USD, _now()),
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS polymarket_paper_position (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_slug TEXT NOT NULL,
                event_title TEXT,
                question TEXT NOT NULL,
                side TEXT NOT NULL,
                yes_token_id TEXT,
                no_token_id TEXT,
                entry_price REAL NOT NULL,
                size_usd REAL NOT NULL,
                shares REAL NOT NULL,
                opened_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                resolution_price REAL,
                closed_at TEXT,
                pnl_usd REAL,
                market_probability_at_entry REAL,
                aria_probability_at_entry REAL,
                edge_at_entry REAL,
                win_probability_at_entry REAL,
                vote_spread_at_entry REAL,
                reasoning TEXT
            )
            """
        )
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def polymarket_paper_enabled() -> bool:
    """Dedicated gate, OFF by default -- same shape as `daily_trade_floor_
    enabled()`/`agent_wallet_pilot_enabled()`. Independent of
    ARIA_PAPER_TRADING_ENABLED (the momentum/VC-thesis $1M test): this is its
    own diagnostic run on a structurally different asset class."""
    return os.environ.get("ARIA_POLYMARKET_PAPER_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


async def starting_capital() -> float:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT starting_capital FROM polymarket_paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    return float(row[0]) if row else STARTING_CAPITAL_USD


async def cash_available() -> float:
    start = await starting_capital()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(size_usd), 0) FROM polymarket_paper_position WHERE status = 'open'"
        ) as cur:
            open_cost = (await cur.fetchone())[0] or 0.0
        async with db.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM polymarket_paper_position WHERE status = 'closed'"
        ) as cur:
            realized = (await cur.fetchone())[0] or 0.0
    return float(start) - float(open_cost) + float(realized)


_POSITION_FIELDS = (
    "id", "event_slug", "event_title", "question", "side", "yes_token_id", "no_token_id",
    "entry_price", "size_usd", "shares", "opened_at", "status", "resolution_price",
    "closed_at", "pnl_usd", "market_probability_at_entry", "aria_probability_at_entry",
    "edge_at_entry", "win_probability_at_entry", "vote_spread_at_entry", "reasoning",
)


def _row_to_dict(row: tuple) -> dict:
    return dict(zip(_POSITION_FIELDS, row))


async def get_open_positions() -> list[dict]:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {','.join(_POSITION_FIELDS)} FROM polymarket_paper_position WHERE status = 'open' "
            "ORDER BY opened_at DESC"
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_closed_positions(limit: int = 500) -> list[dict]:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {','.join(_POSITION_FIELDS)} FROM polymarket_paper_position WHERE status = 'closed' "
            "ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def has_open_position(event_slug: str, question: str) -> bool:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM polymarket_paper_position WHERE status = 'open' AND event_slug = ? AND question = ? LIMIT 1",
            (event_slug, question),
        ) as cur:
            return (await cur.fetchone()) is not None


def compute_bet_size(
    judgment: PolymarketJudgment, entry_price: float, equity_usd: float, *, alloc_multiplier: float = 1.0,
) -> float:
    """Fractional-Kelly sizing on the side ARIA actually bets on.

    ``win_probability``/``entry_price`` are both for the SAME side (never a
    "Yes" probability sized against a "No" entry price or vice versa --
    caller's responsibility, see `open_bet`). Standard binary-bet Kelly:
    buying a share at price P pays $1 on a win (net gain (1-P)/P per $
    staked) or $0 on a loss -- ``b = (1-P)/P``, ``f* = p - (1-p)/b``.

    Clamped to [0, MAX_BET_PCT] -- never negative (would mean "don't bet",
    already excluded upstream by the judgment gates) and never above the
    hard ceiling regardless of what the formula alone suggests.

    ``alloc_multiplier`` (Item #109, 26/07): applied AFTER the hard ceiling,
    never lets the drawdown circuit breaker's soft tier (polymarket_risk_
    guard.SOFT_ALLOC_MULTIPLIER) raise a bet above what Kelly/the ceiling
    alone would already allow -- a multiplier, never a bonus, same doctrine
    as ``risk_guard.py``'s own alloc_multiplier on the momentum side."""
    p = judgment.win_probability
    if p is None or entry_price is None or entry_price <= 0.0 or entry_price >= 1.0:
        return 0.0
    b = (1.0 - entry_price) / entry_price
    kelly_f = p - (1.0 - p) / b if b > 0 else 0.0
    kelly_f = max(0.0, kelly_f)
    fraction = min(kelly_f * KELLY_FRACTION, MAX_BET_PCT)
    return fraction * equity_usd * alloc_multiplier


async def _resolve_entry_price(market: PolymarketCandidateMarket, side: str) -> float | None:
    """Real fill price for the side ARIA is betting on -- the order book's
    best ask (what buying right now would actually cost), never the Gamma
    reference price alone. Degrades to the Gamma price if the book is
    unavailable/empty (never a fabricated price, never a blocked bet on a
    thin book -- same "best effort, never invent" doctrine as the rest of
    this codebase)."""
    token_id = market.yes_token_id if side == "YES" else market.no_token_id
    if token_id:
        book = await polymarket_client.get_order_book(token_id)
        if book.available and book.best_ask is not None:
            return book.best_ask
    if market.yes_price is None:
        return None
    return market.yes_price if side == "YES" else (1.0 - market.yes_price)


async def open_bet(
    market: PolymarketCandidateMarket, judgment: PolymarketJudgment, *, alloc_multiplier: float = 1.0,
) -> dict | None:
    """Books a fictitious bet from an already-decided ``BET`` judgment.
    Returns the position dict, or ``None`` if refused (already positioned on
    this exact market, position cap reached, no real entry price available,
    or the computed size rounds to nothing).

    ``alloc_multiplier`` (Item #109, 26/07): forwarded as-is to
    ``compute_bet_size`` -- resolved ONCE per cycle by the caller via
    ``polymarket_risk_guard.evaluate_portfolio_risk()``, never recomputed
    per candidate (same pattern as the momentum pipeline's own
    ``current_regime``)."""
    if judgment.action != "BET" or not judgment.side:
        return None
    await _ensure_tables()
    if await has_open_position(market.event_slug, market.question):
        return None
    if len(await get_open_positions()) >= MAX_OPEN_POSITIONS:
        return None

    entry_price = await _resolve_entry_price(market, judgment.side)
    if entry_price is None or entry_price <= 0.0 or entry_price >= 1.0:
        return None

    equity = await cash_available()
    size_usd = compute_bet_size(judgment, entry_price, equity, alloc_multiplier=alloc_multiplier)
    cash = await cash_available()
    size_usd = min(size_usd, cash)
    if size_usd <= 1.0:  # not worth booking a near-zero position
        return None

    shares = size_usd / entry_price
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO polymarket_paper_position (
                event_slug, event_title, question, side, yes_token_id, no_token_id,
                entry_price, size_usd, shares, opened_at, status,
                market_probability_at_entry, aria_probability_at_entry, edge_at_entry,
                win_probability_at_entry, vote_spread_at_entry, reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
            """,
            (
                market.event_slug, market.event_title, market.question, judgment.side,
                market.yes_token_id, market.no_token_id, entry_price, size_usd, shares, now,
                judgment.market_probability, judgment.aria_probability, judgment.edge,
                judgment.win_probability, judgment.vote_spread, judgment.reasoning,
            ),
        )
        await db.commit()
        position_id = cur.lastrowid

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {','.join(_POSITION_FIELDS)} FROM polymarket_paper_position WHERE id = ?",
            (position_id,),
        ) as cur2:
            row = await cur2.fetchone()
    return _row_to_dict(row) if row else None


async def check_resolutions() -> list[dict]:
    """Closes any open position whose market has resolved, at the REAL
    payout (1.0 or 0.0 per share, never an approximation) -- never touches a
    still-open market. Returns the freshly-closed positions."""
    await _ensure_tables()
    closed: list[dict] = []
    for pos in await get_open_positions():
        token_id = pos["yes_token_id"] if pos["side"] == "YES" else pos["no_token_id"]
        if not token_id:
            continue
        # `get_market_resolution` always keys off the YES token internally
        # (that's what `clobTokenIds[0]` maps to on Polymarket's own schema)
        # -- a NO position still resolves via the YES token's final price.
        is_resolved, yes_final = await polymarket_client.get_market_resolution(pos["event_slug"], pos["yes_token_id"])
        if not is_resolved or yes_final is None:
            continue

        won = (yes_final == 1.0) if pos["side"] == "YES" else (yes_final == 0.0)
        payout = pos["shares"] * (1.0 if won else 0.0)
        pnl_usd = payout - pos["size_usd"]
        now = _now()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE polymarket_paper_position SET status = 'closed', resolution_price = ?, "
                "closed_at = ?, pnl_usd = ? WHERE id = ?",
                (yes_final, now, pnl_usd, pos["id"]),
            )
            await db.commit()
        pos.update(status="closed", resolution_price=yes_final, closed_at=now, pnl_usd=pnl_usd)
        closed.append(pos)
    return closed


async def portfolio_summary() -> dict:
    await _ensure_tables()
    start = await starting_capital()
    open_positions = await get_open_positions()
    closed_positions = await get_closed_positions(limit=10_000)
    realized_pnl = sum(p["pnl_usd"] or 0.0 for p in closed_positions)
    wins = sum(1 for p in closed_positions if (p["pnl_usd"] or 0.0) > 0)
    equity = start - sum(p["size_usd"] for p in open_positions) + realized_pnl
    return {
        "starting_capital": start,
        "equity": equity,
        "cash": await cash_available(),
        "open_count": len(open_positions),
        "closed_count": len(closed_positions),
        "realized_pnl": realized_pnl,
        "win_rate": (wins / len(closed_positions)) if closed_positions else None,
    }


async def get_equity_high_water_mark() -> float:
    """Highest equity ever reached (Item #109, drawdown circuit breaker) --
    initialized to the starting capital as long as no higher equity has been
    observed yet, never NULL after this call (migrated DBs have the column
    but not the value). Same pattern as paper_trader.get_equity_high_water_
    mark, on this module's own dedicated column."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT equity_high_water_mark FROM polymarket_paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return await starting_capital()


async def set_equity_high_water_mark(value: float) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE polymarket_paper_state SET equity_high_water_mark = ? WHERE id = 1", (value,),
        )
        await db.commit()


def format_bet_alert(pos: dict) -> str:
    return (
        f"[FICTIF] Pari Polymarket -- {pos['side']}\n"
        f"{pos['question']}\n"
        f"Prix d'entrée : {pos['entry_price']:.1%} | Mise : ${pos['size_usd']:,.0f}\n"
        f"Probabilité ARIA : {pos['aria_probability_at_entry']:.1%} "
        f"(marché : {pos['market_probability_at_entry']:.1%})\n"
        + (f"Thèse : {pos['reasoning']}\n" if pos.get("reasoning") else "")
    )


def format_resolution_alert(pos: dict) -> str:
    outcome = "GAGNÉ" if (pos["pnl_usd"] or 0.0) > 0 else "PERDU"
    return (
        f"[FICTIF] Résolution Polymarket -- {outcome}\n"
        f"{pos['question']}\n"
        f"Côté : {pos['side']} | Résultat : {'Oui' if pos['resolution_price'] == 1.0 else 'Non'}\n"
        f"PnL : {pos['pnl_usd']:+,.2f}$"
    )


async def format_portfolio_report(*, recent_closed_limit: int = 5) -> str:
    """Operator-facing snapshot (26/07 -- found missing while reviewing this
    chantier: without this, once activated, there would be no way to check
    the portfolio's state other than scrolling past Telegram alerts).
    Read-only, no network call -- purely reads what's already persisted,
    same doctrine as `/performance`."""
    summary = await portfolio_summary()
    lines = [
        "[FICTIF] Portefeuille Polymarket (paper trading)",
        f"Équité : ${summary['equity']:,.0f} (départ ${summary['starting_capital']:,.0f}) | "
        f"Cash dispo : ${summary['cash']:,.0f}",
        f"Positions ouvertes : {summary['open_count']} | Résolues : {summary['closed_count']}",
    ]
    if summary["win_rate"] is not None:
        lines.append(f"Taux de réussite : {summary['win_rate']:.0%} | PnL réalisé : {summary['realized_pnl']:+,.2f}$")

    # Item #109 (26/07): surfaces the dedicated circuit breaker's state --
    # without this, an operator checking /polymarket after a drawdown would
    # have no way to know WHY new bets stopped appearing.
    from aria_core import polymarket_risk_guard

    blocked, block_reason = polymarket_risk_guard.blocks_new_bets()
    if blocked:
        lines.append(f"⛔ Coupe-circuit armé : {block_reason}")

    open_positions = await get_open_positions()
    if open_positions:
        lines.append("\nPositions ouvertes :")
        for pos in open_positions:
            lines.append(
                f"- {pos['question'][:70]} | {pos['side']} @ {pos['entry_price']:.1%} | ${pos['size_usd']:,.0f}"
            )

    closed = await get_closed_positions(limit=recent_closed_limit)
    if closed:
        lines.append(f"\nDernières résolutions ({len(closed)}) :")
        for pos in closed:
            outcome = "GAGNÉ" if (pos["pnl_usd"] or 0.0) > 0 else "PERDU"
            lines.append(f"- {pos['question'][:70]} | {outcome} ({pos['pnl_usd']:+,.2f}$)")

    return "\n".join(lines)


async def run_polymarket_paper_cycle(notifier=None) -> dict:
    """Full cycle: (1) resolve anything mature (ALWAYS, regardless of the
    circuit breaker's state -- an already-open bet must reach its resolution
    normally), (2) if room/cash/gate/circuit-breaker allow, judge a bounded
    batch of new liquid candidates and book any that clear the bar.

    Item #109 (26/07): ``polymarket_risk_guard.evaluate_portfolio_risk()`` is
    resolved ONCE per cycle, same pattern as ``paper_trader``'s own
    ``risk_guard`` check -- a hard-tier breach (drawdown >= 20% or 5
    consecutive losses) blocks any NEW bet this cycle (existing positions are
    untouched, they only ever wait for resolution), a soft-tier breach halves
    the size of any bet that still gets booked."""
    resolved = await check_resolutions()
    for pos in resolved:
        if notifier:
            await notifier(format_resolution_alert(pos))

    opened: list[dict] = []
    if polymarket_paper_enabled() and len(await get_open_positions()) < MAX_OPEN_POSITIONS:
        from aria_core import polymarket_risk_guard

        risk_state = await polymarket_risk_guard.evaluate_portfolio_risk()
        if notifier and risk_state.newly_triggered_hard:
            await notifier(polymarket_risk_guard.format_hard_drawdown_alert(risk_state))
        elif notifier and risk_state.newly_triggered_soft:
            await notifier(polymarket_risk_guard.format_soft_drawdown_alert(risk_state))

        if not risk_state.blocked:
            candidates = await polymarket_client.list_liquid_events()
            evaluated = 0
            for market in candidates:
                if evaluated >= CANDIDATES_PER_CYCLE:
                    break
                if await has_open_position(market.event_slug, market.question):
                    continue
                evaluated += 1
                judgment = await estimate_market_probability(market)
                if judgment.action != "BET":
                    continue
                position = await open_bet(market, judgment, alloc_multiplier=risk_state.alloc_multiplier)
                if position:
                    opened.append(position)
                    if notifier:
                        await notifier(format_bet_alert(position))

    return {"resolved": resolved, "opened": opened}
