"""Automatic candidate wallet sourcing from ARIA's own history (15/07,
follow-up to #157/#181 -- answer to "who's going to find the wallets?").

Zero new external dependency, zero recurring cost (unlike Nansen/Dune, ruled
out or kept as fallback for this reason): spots tokens ARIA already judged
winners and lists who STILL holds them today (`blockscout.get_token_holders`,
already built) -- a conviction signal (not sold at the first wobble), not a
broad market discovery like a third-party service. Enqueues these addresses
into `wallet_scan_queue.py` -- a source of CANDIDATES TO SCORE, never a
trading signal in itself: the score obtained via `/walletscore`/the
background cycle remains the only signal that matters, same doctrine as the
manual addition via `/walletqueue`.

TWO "winning token" sources combined (15/07, operator observation -- a
single source would have stayed empty for weeks): closed `vc_predictions`
(30-day horizon, slow resolution -- 0 prediction closed as of 11/07 in the
last known audit) AND closed `paper_trader` positions (already active in
prod, resolves much faster via trailing stop/profit-taking on real price).
See `list_strong_performers` for details.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Starting threshold (+100%, i.e. x2) -- adjustable like any ARIA threshold,
# not carved in stone. A token that "only" doubled is still an honest
# signal, not a judgment on what counts as a "winner" for the VC thesis itself.
MIN_OUTCOME_PCT_STRONG_PERFORMER = 100.0

# Don't flood the queue with a single widely-held token.
MAX_HOLDERS_PER_TOKEN = 15

_DEAD_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}


def wallet_candidate_sourcing_enabled() -> bool:
    return os.environ.get("ARIA_WALLET_CANDIDATE_SOURCING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS wallet_candidate_sourcing_processed ("
            "contract TEXT PRIMARY KEY, sourced_at TEXT NOT NULL)"
        )
        await db.commit()


async def _already_sourced(contract: str) -> bool:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM wallet_candidate_sourcing_processed WHERE contract = ?",
                (contract.lower(),),
            )
        ).fetchone()
    return row is not None


async def _mark_sourced(contract: str) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO wallet_candidate_sourcing_processed (contract, sourced_at) VALUES (?, ?)",
            (contract.lower(), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def list_strong_performers(min_outcome_pct: float = MIN_OUTCOME_PCT_STRONG_PERFORMER) -> list[dict]:
    """Tokens ARIA already judged winners, TWO sources combined (15/07,
    operator observation):

    1. Closed `vc_predictions` (30-day horizon, manual/slow resolution --
       0 prediction closed as of 11/07 in the last known audit, so this
       source alone would stay nearly empty for weeks);
    2. Closed `paper_trader` positions (already active in prod, resolves
       much faster -- trailing stop/profit-taking on real price, not a
       fixed calendar horizon) -- no dedicated network column, `network`
       defaults to "base" (confirmed dominant VC/trading pool on Base).

    Reuses both modules as-is, no duplicated table."""
    from aria_core.paper_trader import get_closed_positions
    from aria_core.vc_predictions import list_all_predictions

    predictions = await list_all_predictions()
    from_predictions = [
        {"contract": p["contract"], "network": p.get("network") or "", "outcome_pct": p["outcome_pct"]}
        for p in predictions
        if p.get("status") == "closed"
        and p.get("outcome_pct") is not None
        and p["outcome_pct"] >= min_outcome_pct
        and p.get("contract")
    ]

    closed_positions = await get_closed_positions()
    from_paper_trading = [
        {"contract": pos["contract"], "network": "base", "outcome_pct": pos["pnl_pct"]}
        for pos in closed_positions
        if pos.get("pnl_pct") is not None
        and pos["pnl_pct"] >= min_outcome_pct
        and pos.get("contract")
    ]

    seen: set[str] = set()
    merged: list[dict] = []
    for entry in from_predictions + from_paper_trading:
        contract_l = entry["contract"].lower()
        if contract_l in seen:
            continue
        seen.add(contract_l)
        merged.append(entry)
    return merged


async def _holders_for_token(contract: str, network: str) -> list[str]:
    """22/07 -- explicit operator decision ("let's relieve Blockscout as
    much as possible"): Dune (already built and configured, never wired
    here before this day) tried FIRST via ``get_token_early_buyers`` --
    removes one Blockscout call per winning token. Falls back to Blockscout
    (``get_token_holders``, unchanged historical behavior) if Dune isn't
    configured or fails -- classic dome, never blocking.

    DELIBERATE semantic nuance, not hidden: Dune returns the chronologically
    EARLIEST BUYERS (early conviction), Blockscout returns the biggest
    CURRENT HOLDERS (position held today) -- two different definitions of
    "interesting wallet" on this same token, both valid as candidates to be
    scored by the downstream /walletscore pipeline."""
    from aria_core.services import dune

    chain = network or "base"

    if dune.is_dune_configured():
        dune_result = await dune.get_token_early_buyers(
            contract, blockchain=chain, limit=MAX_HOLDERS_PER_TOKEN,
        )
        if dune_result.available and dune_result.wallets:
            return [
                w for w in dune_result.wallets
                if w.lower() not in _DEAD_ADDRESSES
            ][:MAX_HOLDERS_PER_TOKEN]

    from aria_core.services.blockscout import get_blockscout_client

    client = get_blockscout_client(chain)
    result = await client.get_token_holders(contract)
    if not result.available:
        return []
    # The biggest holder is almost always the DEX pool/router or a locked
    # team allocation -- never an individual "smart wallet". Deliberately
    # simple heuristic (no extra API call per holder to check is_contract --
    # sobriety): documented as imperfect, not a security filter -- the worst
    # case of a false negative here is a noisy /walletscore scan on a
    # contract address, never a risk.
    holders = [
        h for h in result.holders
        if h.address and h.address.lower() not in _DEAD_ADDRESSES
    ][1:]
    return [h.address for h in holders[:MAX_HOLDERS_PER_TOKEN]]


async def run_wallet_candidate_sourcing_cycle(notifier=None) -> dict:
    """One pass: processes ALL winning tokens not yet sourced in a single
    go (15/07, operator observation -- a one-token-per-cycle cap would have
    created an artificial bottleneck independent of the real data rate; if
    several winners are already waiting, process them all NOW rather than
    spreading them over 3h cycles each). For each: enqueues its current
    holders (excluding the biggest holder/dead addresses) into
    `wallet_scan_queue.py`. Triple gate -- `ARIA_WALLET_CANDIDATE_SOURCING_ENABLED`
    on top of `ARIA_WALLET_SCAN_QUEUE_ENABLED`/`ARIA_WALLET_SCORING_ENABLED`
    (all OFF by default) -- fail-closed, respects the kill-switch.

    Honest limitation: this guarantees NO minimum throughput (e.g. "5
    tokens/week") -- it depends on ARIA's real number of winning trades over
    the period, not a code setting. If the real throughput remains
    insufficient once deployed, the only honest lever is to lower
    `MIN_OUTCOME_PCT_STRONG_PERFORMER` (less conviction required per token)
    -- an operator decision, not made silently here."""
    if not wallet_candidate_sourcing_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}

    from aria_core.services.smart_money import wallet_scoring_enabled
    from aria_core.services.wallet_scan_queue import enqueue_wallets, wallet_scan_queue_enabled

    if not wallet_scan_queue_enabled() or not wallet_scoring_enabled():
        return {"outcome": "skipped", "reason": "downstream_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    performers = await list_strong_performers()
    new_candidates = [p for p in performers if not await _already_sourced(p["contract"])]
    if not new_candidates:
        return {"outcome": "no_new_performer"}

    processed: list[dict] = []
    total_sourced = 0
    for candidate in new_candidates:
        holders = await _holders_for_token(candidate["contract"], candidate.get("network") or "")
        await _mark_sourced(candidate["contract"])
        added = await enqueue_wallets(holders) if holders else []
        total_sourced += len(added)
        processed.append({"contract": candidate["contract"], "sourced": len(added)})

    if total_sourced and notifier is not None:
        detail = ", ".join(f"{p['contract'][:10]} ({p['sourced']})" for p in processed if p["sourced"])
        await notifier(
            f"🔍 Sourcing automatique -- {total_sourced} wallet(s) ajouté(s) à la file "
            f"depuis {len(processed)} token(s) gagnant(s) de l'historique ARIA : {detail}."
        )

    return {
        "outcome": "ok",
        "contract": processed[0]["contract"],
        "sourced": processed[0]["sourced"],
        "tokens_processed": processed,
        "total_sourced": total_sourced,
    }
