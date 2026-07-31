"""Manually-submitted discovery candidates (Item #236, 30/07, real operator request:
"je vais en mettre deja une centaine manuellement de mon cote avec /add" --
a token spotted by the operator on DexScreener/elsewhere that the automated
discovery sources (GeckoTerminal top-N, Birdeye, DexScreener boosts/profiles)
haven't surfaced yet -- e.g. REPPO, a real liquid ($757K) Base token found
outside the top-100 GeckoTerminal window this session).

Deliberately NOT a buy shortcut: a manually-added contract joins the SAME
candidate pool as any automatically-discovered one (``discover_momentum_
candidates``), and goes through every existing hard gate (honeypot,
liquidity, volume, wash-trading, holder concentration, R/R) unchanged --
this module only fills the DISCOVERY gap, never the security/quality
gates. A contract that fails a gate is cached by ``momentum_rejection_
cache`` exactly like any other rejected candidate.

Item #241 (30/07): ``add_manual_candidate`` ALSO queues the contract
directly into the GoPlus honeypot watchlist (``services/goplus_watchlist.py``)
at add-time -- decoupled from the buy-decision gates above, which run
liquidity/volume BEFORE honeypot and were routinely rejecting manually-
curated (already real, liquid) tokens before they ever reached the
watchlist at all. Being in the watchlist only means "honeypot status kept
warm in the background", never "cleared to buy".

Persisted (survives redeployments), same doctrine as momentum_blacklist.py.
Expires after ``MANUAL_CANDIDATE_TTL_DAYS`` (7 days, same order of magnitude
as the weekly paper-trading reset) if never bought -- a token that never
forms a signal in a week isn't worth scanning forever, and an expired entry
is silently dropped (never a Telegram alert), same doctrine as a limit
order's own silent expiry."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# 30/07, first-pass value (not yet calibrated against real outcomes) -- long
# enough to give a manually-added token a real chance to form a golden-pocket
# + RSI setup, short enough that a stale entry doesn't linger forever.
MANUAL_CANDIDATE_TTL_DAYS = 7.0


def _normalize_contract(contract: str, chain: str) -> str:
    """Same case-handling as momentum_blacklist.py (Base/EVM tolerates
    lowercase, Solana base58 does not) -- duplicated rather than imported,
    same anti-circular-import doctrine already documented there."""
    contract = (contract or "").strip()
    if (chain or "").strip().lower() != "solana":
        contract = contract.lower()
    return contract


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_candidate_queue (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


async def add_manual_candidate(
    contract: str, chain: str = "base", *, liquidity_usd: float | None = None,
    volume_24h_usd: float | None = None,
) -> bool:
    """Queues a contract for the next discovery cycle. Idempotent (``INSERT
    OR IGNORE`` -- an already-queued contract keeps its original
    ``added_at``, never re-extends its TTL by being submitted twice).
    Returns ``True`` if queued (new or already present), ``False`` if the
    contract/chain was empty.

    Item #241 (30/07, real operator request: "je veux juste qu'ils entrent
    rapidement dans la watchlist"): a real audit found manually-added
    candidates were routinely rejected on ``volume_too_low``/liquidity
    BEFORE ever reaching the honeypot check (``evaluate_hard_gates`` runs
    liquidity/volume/wash-trading gates first, honeypot LAST) -- so most
    never actually reached ``goplus_watchlist`` at all, despite the operator
    having already eyeballed real liquidity/volume on a live screener.
    Also queues directly into the GoPlus honeypot watchlist here,
    DECOUPLED from the buy-decision gates entirely: being in the watchlist
    only means "honeypot status kept warm in the background" (Item #212),
    never "cleared to buy" -- the actual buy decision still runs the exact
    same full gate sequence regardless. ``liquidity_usd``/``volume_24h_usd``
    (real data if already fetched, e.g. by the screenshot-queue resolver's
    own DexScreener match) feed the SAME priority score as any other
    candidate (``goplus_watchlist.compute_priority_score``) -- ``None``
    (the bare ``/add`` case, no fetch performed) degrades to a neutral
    0.0 score, which still claims a slot as long as the watchlist
    (``goplus_watchlist.MAX_WATCHLIST_SIZE``) has room. Best-effort: a
    watchlist-queueing failure never blocks the
    discovery-queue insert itself."""
    await _ensure_table()
    chain = (chain or "base").strip().lower()
    contract = _normalize_contract(contract, chain)
    if not contract or not chain:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO manual_candidate_queue (contract, chain, added_at) VALUES (?, ?, ?)",
            (contract, chain, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    try:
        from aria_core.services import goplus_watchlist

        score = goplus_watchlist.compute_priority_score(liquidity_usd, volume_24h_usd)
        await goplus_watchlist.add_or_touch(contract, chain, score)
    except Exception as exc:  # noqa: BLE001 -- never blocks the discovery-queue insert
        logger.info("add_manual_candidate: goplus_watchlist queueing failed for %s (%s)", contract, exc)

    return True


async def list_pending_manual_candidates() -> list[dict]:
    """Every still-fresh manually-queued contract -- checked by
    ``discover_momentum_candidates`` on every discovery cycle. Opportunistic
    purge of expired rows first (same low-cost hygiene as ``momentum_
    rejection_cache.record_rejection``), so this table never grows
    unbounded."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MANUAL_CANDIDATE_TTL_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM manual_candidate_queue WHERE added_at < ?", (cutoff,))
        await db.commit()
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM manual_candidate_queue ORDER BY added_at ASC")
        ).fetchall()
    return [dict(r) for r in rows]


async def reconcile_watchlist_membership(entries: list[dict]) -> int:
    """Item #241 follow-up (31/07, real gap found live): ``add_manual_candidate``
    only ever ATTEMPTS the ``goplus_watchlist`` insert ONCE, at add-time --
    ``add_or_touch`` returning ``False`` (watchlist full at that exact moment,
    this candidate's neutral 0.0 score didn't beat the current worst entry)
    is silent and never retried. Confirmed live: 97 of 161 operator-submitted
    candidates (screenshot batch, 30/07) never got a slot, even though the
    watchlist had since freed up hundreds of slots (entries evicted via
    confirmed-honeypot removal) -- they simply never got a second attempt.
    Called once per discovery cycle (~15min) with the SAME ``manual_entries``
    ``discover_momentum_candidates`` already fetched -- a single query finds
    the genuinely-missing subset (never re-writes an already-present entry),
    then retries only those, best-effort. Zero network cost (``add_or_touch``
    is a pure local DB check). Returns the number actually added this pass."""
    if not entries:
        return 0
    await _ensure_table()
    from aria_core.services import goplus_watchlist

    rows = await goplus_watchlist.list_all()
    present = {(r["contract"].lower(), r["chain"].lower()) for r in rows}

    added = 0
    for entry in entries:
        contract = _normalize_contract(entry["contract"], entry["chain"])
        chain = (entry["chain"] or "").strip().lower()
        if not contract or not chain or (contract, chain) in present:
            continue
        try:
            ok = await goplus_watchlist.add_or_touch(contract, chain, 0.0)
        except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks discovery
            logger.info("reconcile_watchlist_membership: failed for %s/%s (%s)", contract, chain, exc)
            continue
        if ok:
            added += 1
    return added


async def remove_manual_candidate(contract: str, chain: str) -> None:
    """Called once a manually-added candidate is bought -- no longer needs
    re-discovery every cycle (``paper_trader.has_open`` already prevents a
    double-buy, but there's no reason to keep re-scanning a filled order)."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM manual_candidate_queue WHERE contract = ? AND chain = ?", (contract, chain),
        )
        await db.commit()
